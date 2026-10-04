"""Canvas editing endpoints for MCP clients.

Exposes the in-app assistant's own tools (services.chat.tools.ChatTools) as one
POST route per tool, so a coding agent connected over MCP can edit a deck with
the same tools Presenton's internal LLM uses. See MODS.md.
"""

import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from llmai.shared import AssistantToolCall  # type: ignore[import-not-found]
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import func, select

from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services.canvas_tool_registry import (
    CanvasTool,
    DeckType,
    get_canvas_tools,
    get_canvas_tools_for_deck,
)
from services.chat.presentation_context_store import PresentationContextStore
from services.chat.prompts import build_system_prompt
from services.chat.tools import ChatTools
from services.database import get_async_session
from utils.mcp_public_urls import absolute_mcp_result_links

CANVAS_ROUTER = APIRouter(prefix="/canvas", tags=["Canvas"])
LOGGER = logging.getLogger(__name__)

# Slide index changes read the deck, compute new indices and write them back
# without locking the presentation row. This matches upstream's own slide
# mutations (PresentationChatMemoryLayer), which the tool routes below reuse.

EDITING_GUIDE_PREFIX = """\
Presenton's built-in assistant follows the protocol below with the same tools
you have over MCP. Differences for MCP clients:
- Every tool also takes `presentation_id`.
- Tool names: use the MCP name from `tools` in this response (the guide uses the
  assistant's names, the keys of `tools`).
- There is no chat user; ignore instructions about the final chat reply and
  report results to your user instead. Share `edit_path` so they can open the deck.
"""


# The assistant's tools report some rejections inside a successful result
# (e.g. {"deleted": False, "message": "No slide found ..."}) rather than by
# raising; treat those as failures too. The one idempotent no-op stays a
# success: updateComponent's layer move on a component already at that layer
# ({"updated": False, "action": ..., "message": "Component 'x' is already at
# that layer."}). It is matched on its fixed message suffix plus the "action"
# key, never on caller-supplied text inside the message.
REJECTION_KEYS = ("added", "deleted", "saved", "updated")
LAYER_NO_OP_SUFFIX = "is already at that layer."


def _is_rejection(result: object) -> bool:
    if not isinstance(result, dict):
        return False
    if not any(result.get(key) is False for key in REJECTION_KEYS):
        return False
    layer_no_op = "action" in result and str(result.get("message") or "").endswith(LAYER_NO_OP_SUFFIX)
    return not layer_no_op


def _deck_type(presentation: PresentationModel) -> DeckType:
    return "smart" if presentation.generation_mode == "smart" else "standard"


def _edit_path(presentation_id: uuid.UUID) -> str:
    return f"/presentation?id={presentation_id}"


async def _get_presentation(
    sql_session: AsyncSession, presentation_id: uuid.UUID
) -> PresentationModel:
    presentation = await sql_session.get(PresentationModel, presentation_id)
    if not presentation:
        raise HTTPException(status_code=404, detail="Presentation not found")
    return presentation


class CanvasContextSlide(BaseModel):
    index: int
    id: uuid.UUID
    layout: str


class CanvasContextResponse(BaseModel):
    presentation_id: uuid.UUID
    title: Optional[str] = None
    generation_mode: DeckType
    n_slides: int
    slides: list[CanvasContextSlide]
    tools: dict[str, str]
    editing_guide: str
    edit_path: str


@CANVAS_ROUTER.get(
    "/presentation/{presentation_id}/context",
    response_model=CanvasContextResponse,
)
async def get_canvas_context(
    presentation_id: uuid.UUID,
    request: Request,
    sql_session: AsyncSession = Depends(get_async_session),
):
    """Start here: deck type, slide list, available tools and the editing protocol."""
    presentation = await _get_presentation(sql_session, presentation_id)
    deck_type = _deck_type(presentation)

    slides = await sql_session.scalars(
        select(SlideModel)
        .where(SlideModel.presentation == presentation_id)
        .order_by(SlideModel.index)
    )

    payload = absolute_mcp_result_links(
        request,
        {
            "presentation_id": presentation.id,
            "title": presentation.title,
            "generation_mode": deck_type,
            "n_slides": presentation.n_slides,
            "slides": [
                CanvasContextSlide(index=slide.index, id=slide.id, layout=slide.layout)
                for slide in slides
            ],
            "tools": {
                tool.tool_name: tool.mcp_name
                for tool in get_canvas_tools_for_deck(deck_type)
            },
            "editing_guide": EDITING_GUIDE_PREFIX
            + "\n"
            + build_system_prompt("", "", presentation_type=deck_type),
            "edit_path": _edit_path(presentation.id),
        },
    )
    return CanvasContextResponse(**payload)


class ReorderSlideRequest(BaseModel):
    new_index: int


@CANVAS_ROUTER.patch("/slide/{slide_id}/reorder", response_model=dict)
async def canvas_reorder_slide(
    slide_id: uuid.UUID,
    request: ReorderSlideRequest,
    api_request: Request,
    sql_session: AsyncSession = Depends(get_async_session),
):
    """Move a slide to a new 0-based index; other slides shift to make room."""
    slide = await sql_session.get(SlideModel, slide_id)
    if not slide:
        raise HTTPException(status_code=404, detail="Slide not found")

    presentation = await _get_presentation(sql_session, slide.presentation)

    statement = (
        select(SlideModel)
        .where(SlideModel.presentation == presentation.id)
        .order_by(SlideModel.index.asc())
    )
    results = await sql_session.execute(statement)
    slides = results.scalars().all()

    max_index = len(slides) - 1
    target_index = max(0, min(request.new_index, max_index))

    old_index = slide.index
    if old_index != target_index:
        slide.index = target_index
        sql_session.add(slide)

        for s in slides:
            if s.id == slide.id:
                continue
            if old_index < target_index and old_index <= s.index <= target_index:
                s.index -= 1
                sql_session.add(s)
            elif target_index <= s.index < old_index:
                s.index += 1
                sql_session.add(s)

        await sql_session.commit()

    return absolute_mcp_result_links(
        api_request,
        {
            "slide": slide.model_dump(),
            "edit_path": _edit_path(presentation.id),
        },
    )


async def _run_canvas_tool(
    tool: CanvasTool,
    presentation_id: uuid.UUID,
    api_request: Request,
    sql_session: AsyncSession,
) -> dict:
    presentation = await _get_presentation(sql_session, presentation_id)
    deck_type = _deck_type(presentation)
    if deck_type not in tool.deck_types:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{tool.mcp_name} is not available for {deck_type} decks. "
                "Call get_presentation_context for this deck's tools."
            ),
        )

    # Arguments are passed through unvalidated so the assistant's own
    # argument repair (e.g. objects where the schema asks for JSON strings)
    # applies exactly as it does in the in-app chat.
    raw_body = await api_request.body()
    try:
        arguments = json.loads(raw_body) if raw_body.strip() else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON body: {exc}") from exc
    if not isinstance(arguments, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object.")

    chat_tools = ChatTools(
        PresentationContextStore(sql_session, presentation.id, presentation_type=deck_type)
    )
    outcome = await chat_tools.execute_tool_call(
        AssistantToolCall(
            id=f"mcp_{uuid.uuid4().hex}",
            name=tool.tool_name,
            arguments=json.dumps(arguments),
        )
    )
    if not outcome.get("ok") or _is_rejection(outcome.get("result")):
        await sql_session.rollback()
        # Keep the assistant's error or rejection message, repair notes and
        # recovery guidance.
        raise HTTPException(status_code=422, detail=outcome)

    # Upstream's save_slide (addNewSlideLayout, saveSlide) inserts and shifts
    # slides without updating n_slides; keep it in sync for every tool.
    slide_count = await sql_session.scalar(
        select(func.count()).select_from(SlideModel).where(SlideModel.presentation == presentation.id)
    )
    if presentation.n_slides != slide_count:
        presentation.n_slides = slide_count
        sql_session.add(presentation)
    await sql_session.commit()
    response = {"result": outcome.get("result"), "edit_path": _edit_path(presentation.id)}
    if outcome.get("repair"):
        response["repair"] = outcome["repair"]
    return absolute_mcp_result_links(api_request, response)


def _register_canvas_tool(tool: CanvasTool) -> None:
    async def run_canvas_tool(
        presentation_id: uuid.UUID,
        api_request: Request,
        sql_session: AsyncSession = Depends(get_async_session),
    ):
        return await _run_canvas_tool(tool, presentation_id, api_request, sql_session)

    CANVAS_ROUTER.add_api_route(
        tool.path,
        run_canvas_tool,
        methods=["POST"],
        response_model=dict,
        operation_id=tool.operation_id,
        summary=tool.mcp_name,
        description=tool.description,
        openapi_extra={
            "requestBody": {
                "required": bool(tool.input_schema.get("required")),
                "content": {"application/json": {"schema": tool.input_schema}},
            }
        },
    )


for _tool in get_canvas_tools():
    _register_canvas_tool(_tool)
