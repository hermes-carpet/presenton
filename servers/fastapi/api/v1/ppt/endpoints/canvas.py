import logging
import uuid
from typing import Annotated, Any, Dict, Optional, Tuple

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services.database import get_async_session
from services.image_generation_service import ImageGenerationService
from services.mem0_presentation_memory_service import MEM0_PRESENTATION_MEMORY_SERVICE
from utils.asset_directory_utils import get_images_directory
from utils.llm_calls.edit_slide import get_edited_slide_content
from utils.llm_calls.edit_slide_html import get_edited_slide_html
from utils.llm_calls.select_slide_type_on_edit import get_slide_layout_from_prompt
from utils.process_slides import (
    image_target_sizes_from_template,
    process_old_and_new_slides_and_fetch_assets,
    process_slide_and_fetch_assets,
)
from utils.schema_utils import get_schema_validation_errors
from api.v1.ppt.endpoints.presentation import (
    _apply_template_content_to_ui,
    _template_slide_ui,
)


CANVAS_ROUTER = APIRouter(prefix="/canvas", tags=["Canvas"])
LOGGER = logging.getLogger(__name__)


def _is_template_layout_payload(layout: object) -> bool:
    return isinstance(layout, dict) and isinstance(layout.get("layouts"), list)


class EditSlideRequest(BaseModel):
    prompt: str
    language: Optional[str] = None
    tone: Optional[str] = None
    verbosity: Optional[str] = None


class CanvasSlideResponse(BaseModel):
    slide: SlideModel
    edit_path: str


@CANVAS_ROUTER.post("/slide/{slide_id}/edit", response_model=CanvasSlideResponse)
async def canvas_edit_slide(
    slide_id: uuid.UUID,
    request: EditSlideRequest,
    sql_session: AsyncSession = Depends(get_async_session),
):
    slide = await sql_session.get(SlideModel, slide_id)
    if not slide:
        raise HTTPException(status_code=404, detail="Slide not found")
    presentation = await sql_session.get(PresentationModel, slide.presentation)
    if not presentation:
        raise HTTPException(status_code=404, detail="Presentation not found")

    memory_context = await MEM0_PRESENTATION_MEMORY_SERVICE.retrieve_context(
        presentation.id,
        request.prompt,
    )

    presentation_layout = presentation.get_layout()
    slide_layout = await get_slide_layout_from_prompt(
        request.prompt,
        presentation_layout,
        slide,
        memory_context,
    )

    edited_slide_content = await get_edited_slide_content(
        request.prompt,
        slide,
        request.language or presentation.language,
        slide_layout,
        request.tone or presentation.tone,
        request.verbosity or presentation.verbosity,
        presentation.instructions,
        memory_context,
    )

    image_generation_service = ImageGenerationService(get_images_directory())

    image_warnings: list[dict] = []
    new_assets = await process_old_and_new_slides_and_fetch_assets(
        image_generation_service,
        slide.content,
        edited_slide_content,
        icon_weight=presentation_layout.icon_weight,
        use_template_asset_fields=(
            _is_template_layout_payload(presentation.layout)
            or isinstance(slide.ui, dict)
        ),
        allow_image_fallback=True,
        image_warnings=image_warnings,
        old_image_target_sizes=image_target_sizes_from_template(
            _template_slide_ui(presentation.layout, slide.layout) or slide.ui,
            slide.content,
            _apply_template_content_to_ui,
        ),
        new_image_target_sizes=image_target_sizes_from_template(
            _template_slide_ui(presentation.layout, slide_layout.id),
            edited_slide_content,
            _apply_template_content_to_ui,
        ),
    )
    for warning in image_warnings:
        LOGGER.warning(
            "Canvas slide edit image warning: slide_id=%s detail=%s",
            slide.id,
            warning.get("detail"),
        )

    # Note: we mutate the slide in place as requested, or recreate if we strictly follow edit_slide.
    # We will give it a new UUID to force NextJS updates
    slide.id = uuid.uuid4()
    sql_session.add(slide)
    slide.content = edited_slide_content
    slide.layout = slide_layout.id
    slide.speaker_note = edited_slide_content.get("__speaker_note__", "")
    sql_session.add_all(new_assets)
    await sql_session.commit()

    await MEM0_PRESENTATION_MEMORY_SERVICE.store_slide_edit(
        presentation_id=presentation.id,
        slide_index=slide.index,
        edit_prompt=request.prompt,
        edited_slide_content=edited_slide_content,
    )

    return CanvasSlideResponse(
        slide=slide,
        edit_path=f"/presentation?id={presentation.id}",
    )


class EditSlideHtmlRequest(BaseModel):
    prompt: str
    current_html: str


@CANVAS_ROUTER.post("/slide/{slide_id}/edit-html", response_model=CanvasSlideResponse)
async def canvas_edit_slide_html(
    slide_id: uuid.UUID,
    request: EditSlideHtmlRequest,
    sql_session: AsyncSession = Depends(get_async_session),
):
    slide = await sql_session.get(SlideModel, slide_id)
    if not slide:
        raise HTTPException(status_code=404, detail="Slide not found")

    presentation = await sql_session.get(PresentationModel, slide.presentation)
    if not presentation:
        raise HTTPException(status_code=404, detail="Presentation not found")

    memory_context = await MEM0_PRESENTATION_MEMORY_SERVICE.retrieve_context(
        presentation.id,
        request.prompt,
    )

    edited_slide_html = await get_edited_slide_html(
        request.prompt,
        request.current_html,
        memory_context,
    )

    slide.id = uuid.uuid4()
    sql_session.add(slide)
    slide.html_content = edited_slide_html
    await sql_session.commit()

    await MEM0_PRESENTATION_MEMORY_SERVICE.store_slide_edit(
        presentation_id=presentation.id,
        slide_index=slide.index,
        edit_prompt=request.prompt,
        edited_slide_content=edited_slide_html,
    )

    return CanvasSlideResponse(
        slide=slide,
        edit_path=f"/presentation?id={presentation.id}",
    )


class ValidateJsonRequest(BaseModel):
    presentation_id: uuid.UUID
    layout_id: str
    content: Dict[str, Any]


class ValidateJsonResponse(BaseModel):
    valid: bool
    errors: list[str]


@CANVAS_ROUTER.post("/validate-json", response_model=ValidateJsonResponse)
async def canvas_validate_json(
    request: ValidateJsonRequest,
    sql_session: AsyncSession = Depends(get_async_session),
):
    presentation = await sql_session.get(PresentationModel, request.presentation_id)
    if not presentation:
        raise HTTPException(status_code=404, detail="Presentation not found")

    presentation_layout = presentation.get_layout()
    slide_layout = next(
        (l for l in presentation_layout.slides if l.id == request.layout_id), None
    )
    if not slide_layout:
        raise HTTPException(status_code=404, detail="Layout not found")

    errors = get_schema_validation_errors(slide_layout.json_schema, request.content)
    return ValidateJsonResponse(
        valid=len(errors) == 0,
        errors=errors,
    )


class CreateSlideRequest(BaseModel):
    presentation_id: uuid.UUID
    layout_id: str
    content: Dict[str, Any]
    index: Optional[int] = None


@CANVAS_ROUTER.post("/slide/create", response_model=CanvasSlideResponse)
async def canvas_create_slide(
    request: CreateSlideRequest,
    sql_session: AsyncSession = Depends(get_async_session),
):
    presentation = await sql_session.get(PresentationModel, request.presentation_id)
    if not presentation:
        raise HTTPException(status_code=404, detail="Presentation not found")

    presentation_layout = presentation.get_layout()
    slide_layout = next(
        (l for l in presentation_layout.slides if l.id == request.layout_id), None
    )
    if not slide_layout:
        raise HTTPException(status_code=404, detail="Layout not found")

    errors = get_schema_validation_errors(slide_layout.json_schema, request.content)
    if errors:
        raise HTTPException(
            status_code=400, detail={"message": "Invalid JSON content", "errors": errors}
        )

    image_generation_service = ImageGenerationService(get_images_directory())

    image_warnings: list[dict] = []
    assets = await process_slide_and_fetch_assets(
        image_generation_service,
        request.content,
        icon_weight=presentation_layout.icon_weight,
        use_template_asset_fields=_is_template_layout_payload(presentation.layout),
        allow_image_fallback=True,
        image_warnings=image_warnings,
        image_target_sizes=image_target_sizes_from_template(
            _template_slide_ui(presentation.layout, slide_layout.id),
            request.content,
            _apply_template_content_to_ui,
        ),
    )

    for warning in image_warnings:
        LOGGER.warning(
            "Canvas create slide image warning: detail=%s",
            warning.get("detail"),
        )

    # Figure out the index
    statement = (
        select(SlideModel)
        .where(SlideModel.presentation == presentation.id)
        .order_by(SlideModel.index.asc())
    )
    results = await sql_session.execute(statement)
    slides = results.scalars().all()

    target_index = request.index if request.index is not None else len(slides)

    # Shift indices if necessary
    for s in slides:
        if s.index >= target_index:
            s.index += 1
            sql_session.add(s)

    new_slide = SlideModel(
        id=uuid.uuid4(),
        owner_id=presentation.owner_id,
        presentation=presentation.id,
        layout_group=slide_layout.group,
        layout=slide_layout.id,
        index=target_index,
        content=request.content,
        speaker_note=request.content.get("__speaker_note__", ""),
    )

    sql_session.add(new_slide)
    sql_session.add_all(assets)
    presentation.n_slides += 1
    sql_session.add(presentation)
    await sql_session.commit()

    return CanvasSlideResponse(
        slide=new_slide,
        edit_path=f"/presentation?id={presentation.id}",
    )
