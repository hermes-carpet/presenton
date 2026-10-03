import logging
import uuid
from typing import Any, Dict, Optional, List

from fastapi import APIRouter, Depends, HTTPException, Request
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
from utils.mcp_public_urls import absolute_mcp_result_links
from api.v1.ppt.endpoints.presentation import (
    _apply_template_content_to_ui,
    _template_slide_ui,
    _get_presentation_stream_layout,
)
from models.presentation_with_slides import PresentationWithSlides
from api.v1.ppt.endpoints.presentation import _presentation_response_data
from constants.presentation import MAX_NUMBER_OF_SLIDES

CANVAS_ROUTER = APIRouter(prefix="/canvas", tags=["Canvas"])
LOGGER = logging.getLogger(__name__)


def _is_template_layout_payload(layout: object) -> bool:
    return isinstance(layout, dict) and isinstance(layout.get("layouts"), list)


class EditSlideRequest(BaseModel):
    prompt: str
    language: Optional[str] = None
    tone: Optional[str] = None
    verbosity: Optional[str] = None


class CanvasContextLayout(BaseModel):
    id: str
    name: Optional[str] = None
    description: Optional[str] = None

class CanvasContextResponse(BaseModel):
    presentation: PresentationWithSlides
    generation_mode: str
    layouts: List[CanvasContextLayout]

@CANVAS_ROUTER.get("/presentation/{presentation_id}/context", response_model=CanvasContextResponse)
async def get_canvas_context(
    presentation_id: uuid.UUID,
    request: Request,
    sql_session: AsyncSession = Depends(get_async_session),
):
    presentation = await sql_session.get(PresentationModel, presentation_id)
    if not presentation:
        raise HTTPException(status_code=404, detail="Presentation not found")

    slides_result = await sql_session.scalars(
        select(SlideModel)
        .where(SlideModel.presentation == presentation_id)
        .order_by(SlideModel.index)
    )
    slides = list(slides_result)

    presentation_with_slides = PresentationWithSlides(
        **_presentation_response_data(presentation),
        slides=slides,
    )

    layouts = []
    if presentation.layout is not None:
        layout = _get_presentation_stream_layout(presentation)
        if layout:
            layouts = [CanvasContextLayout(id=l.id, name=l.name, description=l.description) for l in layout.slides]

    return CanvasContextResponse(
        presentation=presentation_with_slides,
        generation_mode=presentation.generation_mode,
        layouts=layouts,
    )

@CANVAS_ROUTER.post("/slide/{slide_id}/edit", response_model=dict)
async def canvas_edit_slide(
    slide_id: uuid.UUID,
    request: EditSlideRequest,
    api_request: Request,
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

    if presentation.layout is None:
        raise HTTPException(status_code=400, detail="Cannot edit slide: presentation has no layouts.")
    presentation_layout = _get_presentation_stream_layout(presentation)
    if not presentation_layout:
        raise HTTPException(status_code=400, detail="Cannot edit slide: presentation has no layouts.")

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
    slide.layout_group = presentation_layout.name
    slide.speaker_note = edited_slide_content.get("__speaker_note__", "")
    slide.ui = _template_slide_ui(presentation.layout, slide.layout)
    slide.ui = _apply_template_content_to_ui(slide.ui, slide.content)
    sql_session.add_all(new_assets)

    # Re-hydrate UI after asset fetching mutated the slide's content
    slide.ui = _apply_template_content_to_ui(slide.ui, slide.content)

    await sql_session.commit()

    await MEM0_PRESENTATION_MEMORY_SERVICE.store_slide_edit(
        presentation_id=presentation.id,
        slide_index=slide.index,
        edit_prompt=request.prompt,
        edited_slide_content=edited_slide_content,
    )

    return absolute_mcp_result_links(
        api_request,
        {
            "slide": slide.model_dump(),
            "edit_path": f"/presentation?id={presentation.id}",
        },
    )


class EditSlideHtmlRequest(BaseModel):
    prompt: str
    current_html: Optional[str] = None


@CANVAS_ROUTER.post("/slide/{slide_id}/edit-html", response_model=dict)
async def canvas_edit_slide_html(
    slide_id: uuid.UUID,
    request: EditSlideHtmlRequest,
    api_request: Request,
    sql_session: AsyncSession = Depends(get_async_session),
):
    slide = await sql_session.get(SlideModel, slide_id)
    if not slide:
        raise HTTPException(status_code=404, detail="Slide not found")

    presentation = await sql_session.get(PresentationModel, slide.presentation)
    if not presentation:
        raise HTTPException(status_code=404, detail="Presentation not found")

    html_to_edit = request.current_html or slide.html_content
    if not html_to_edit:
        raise HTTPException(status_code=400, detail="No HTML to edit")

    memory_context = await MEM0_PRESENTATION_MEMORY_SERVICE.retrieve_context(
        presentation.id,
        request.prompt,
    )

    edited_slide_html = await get_edited_slide_html(
        request.prompt,
        html_to_edit,
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

    return absolute_mcp_result_links(
        api_request,
        {
            "slide": slide.model_dump(),
            "edit_path": f"/presentation?id={presentation.id}",
        },
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

    if presentation.layout is None:
        raise HTTPException(status_code=400, detail="Cannot validate JSON: presentation has no layouts.")
    presentation_layout = _get_presentation_stream_layout(presentation)
    if not presentation_layout:
        raise HTTPException(status_code=400, detail="Cannot validate JSON: presentation has no layouts.")
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


class UpdateSlideHtmlRequest(BaseModel):
    html: str

@CANVAS_ROUTER.patch("/slide/{slide_id}/html", response_model=dict)
async def canvas_update_slide_html(
    slide_id: uuid.UUID,
    request: UpdateSlideHtmlRequest,
    api_request: Request,
    sql_session: AsyncSession = Depends(get_async_session),
):
    slide = await sql_session.get(SlideModel, slide_id)
    if not slide:
        raise HTTPException(status_code=404, detail="Slide not found")

    presentation = await sql_session.get(PresentationModel, slide.presentation)
    if not presentation:
        raise HTTPException(status_code=404, detail="Presentation not found")

    if presentation.generation_mode != "smart":
        raise HTTPException(status_code=400, detail="HTML update is only supported for Smart decks.")

    slide.id = uuid.uuid4()
    sql_session.add(slide)
    slide.html_content = request.html
    await sql_session.commit()

    return absolute_mcp_result_links(
        api_request,
        {
            "slide": slide.model_dump(),
            "edit_path": f"/presentation?id={presentation.id}",
        },
    )

class CreateSlideRequest(BaseModel):
    presentation_id: uuid.UUID
    layout_id: str
    content: Dict[str, Any]
    index: Optional[int] = None


@CANVAS_ROUTER.post("/slide/create", response_model=dict)
async def canvas_create_slide(
    request: CreateSlideRequest,
    api_request: Request,
    sql_session: AsyncSession = Depends(get_async_session),
):
    presentation = await sql_session.get(PresentationModel, request.presentation_id)
    if not presentation:
        raise HTTPException(status_code=404, detail="Presentation not found")

    if presentation.layout is None:
        raise HTTPException(status_code=400, detail="Cannot create slide: presentation has no layouts.")
    presentation_layout = _get_presentation_stream_layout(presentation)
    if not presentation_layout:
        raise HTTPException(status_code=400, detail="Cannot create slide: presentation has no layouts.")
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

    # Figure out the index
    statement = (
        select(SlideModel)
        .where(SlideModel.presentation == presentation.id)
        .order_by(SlideModel.index.asc())
    )
    results = await sql_session.execute(statement)
    slides = results.scalars().all()

    if len(slides) >= MAX_NUMBER_OF_SLIDES:
        raise HTTPException(status_code=400, detail=f"Cannot exceed maximum slide limit ({MAX_NUMBER_OF_SLIDES}).")

    target_index = request.index if request.index is not None else len(slides)
    target_index = max(0, min(target_index, len(slides)))

    # Shift indices if necessary
    for s in slides:
        if getattr(s, 'index', -1) >= target_index:
            s.index += 1
            sql_session.add(s)

    new_slide = SlideModel(
        id=uuid.uuid4(),
        owner_id=presentation.owner_id,
        presentation=presentation.id,
        layout_group=presentation_layout.name,
        layout=slide_layout.id,
        index=target_index,
        content=request.content,
        speaker_note=request.content.get("__speaker_note__", ""),
        ui=_template_slide_ui(presentation.layout, slide_layout.id),
    )
    new_slide.ui = _apply_template_content_to_ui(new_slide.ui, new_slide.content)

    image_warnings: list[dict] = []
    assets = await process_slide_and_fetch_assets(
        image_generation_service,
        new_slide,
        icon_weight=presentation_layout.icon_weight,
        allow_image_fallback=True,
        image_warnings=image_warnings,
        image_target_sizes=image_target_sizes_from_template(
            new_slide.ui,
            new_slide.content,
            _apply_template_content_to_ui,
        ),
    )

    for warning in image_warnings:
        LOGGER.warning(
            "Canvas create slide image warning: detail=%s",
            warning.get("detail"),
        )

    # Re-hydrate UI after asset fetching mutated the slide's content
    new_slide.ui = _apply_template_content_to_ui(new_slide.ui, new_slide.content)

    sql_session.add(new_slide)
    sql_session.add_all(assets)
    presentation.n_slides = (presentation.n_slides or 0) + 1
    sql_session.add(presentation)
    await sql_session.commit()

    return absolute_mcp_result_links(
        api_request,
        {
            "slide": new_slide.model_dump(),
            "edit_path": f"/presentation?id={presentation.id}",
        },
    )

class UpdateSlideRequest(BaseModel):
    layout_id: str
    content: Dict[str, Any]

@CANVAS_ROUTER.patch("/slide/{slide_id}", response_model=dict)
async def canvas_update_slide(
    slide_id: uuid.UUID,
    request: UpdateSlideRequest,
    api_request: Request,
    sql_session: AsyncSession = Depends(get_async_session),
):
    slide = await sql_session.get(SlideModel, slide_id)
    if not slide:
        raise HTTPException(status_code=404, detail="Slide not found")

    presentation = await sql_session.get(PresentationModel, slide.presentation)
    if not presentation:
        raise HTTPException(status_code=404, detail="Presentation not found")

    if presentation.layout is None:
        raise HTTPException(status_code=400, detail="Cannot update slide: presentation has no layouts.")
    presentation_layout = _get_presentation_stream_layout(presentation)
    if not presentation_layout:
        raise HTTPException(status_code=400, detail="Cannot update slide: presentation has no layouts.")

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
    new_assets = await process_old_and_new_slides_and_fetch_assets(
        image_generation_service,
        slide.content,
        request.content,
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
            request.content,
            _apply_template_content_to_ui,
        ),
    )

    for warning in image_warnings:
        LOGGER.warning(
            "Canvas slide update image warning: slide_id=%s detail=%s",
            slide.id,
            warning.get("detail"),
        )

    slide.id = uuid.uuid4()
    sql_session.add(slide)
    slide.content = request.content
    slide.layout = slide_layout.id
    slide.layout_group = presentation_layout.name
    slide.speaker_note = request.content.get("__speaker_note__", "")
    slide.ui = _template_slide_ui(presentation.layout, slide.layout)
    slide.ui = _apply_template_content_to_ui(slide.ui, slide.content)

    sql_session.add_all(new_assets)

    # Re-hydrate UI after asset fetching mutated the slide's content
    slide.ui = _apply_template_content_to_ui(slide.ui, slide.content)

    await sql_session.commit()

    return absolute_mcp_result_links(
        api_request,
        {
            "slide": slide.model_dump(),
            "edit_path": f"/presentation?id={presentation.id}",
        },
    )

@CANVAS_ROUTER.delete("/slide/{slide_id}", response_model=dict)
async def canvas_delete_slide(
    slide_id: uuid.UUID,
    api_request: Request,
    sql_session: AsyncSession = Depends(get_async_session),
):
    slide = await sql_session.get(SlideModel, slide_id)
    if not slide:
        raise HTTPException(status_code=404, detail="Slide not found")

    presentation = await sql_session.get(PresentationModel, slide.presentation)
    if not presentation:
        raise HTTPException(status_code=404, detail="Presentation not found")

    deleted_index = slide.index
    await sql_session.delete(slide)

    # Update indices of subsequent slides
    statement = (
        select(SlideModel)
        .where(SlideModel.presentation == presentation.id)
        .where(SlideModel.index > deleted_index)
    )
    results = await sql_session.execute(statement)
    subsequent_slides = results.scalars().all()

    for s in subsequent_slides:
        if getattr(s, 'index', -1) != -1:
            s.index -= 1
            sql_session.add(s)

    presentation.n_slides = max(0, (presentation.n_slides or 1) - 1)
    sql_session.add(presentation)
    await sql_session.commit()

    return absolute_mcp_result_links(
        api_request,
        {
            "success": True,
            "edit_path": f"/presentation?id={presentation.id}",
        },
    )

class ReorderSlideRequest(BaseModel):
    new_index: int

@CANVAS_ROUTER.patch("/slide/{slide_id}/reorder", response_model=dict)
async def canvas_reorder_slide(
    slide_id: uuid.UUID,
    request: ReorderSlideRequest,
    api_request: Request,
    sql_session: AsyncSession = Depends(get_async_session),
):
    slide = await sql_session.get(SlideModel, slide_id)
    if not slide:
        raise HTTPException(status_code=404, detail="Slide not found")

    presentation = await sql_session.get(PresentationModel, slide.presentation)
    if not presentation:
        raise HTTPException(status_code=404, detail="Presentation not found")

    statement = (
        select(SlideModel)
        .where(SlideModel.presentation == presentation.id)
        .order_by(SlideModel.index.asc())
    )
    results = await sql_session.execute(statement)
    slides = results.scalars().all()

    max_index = len(slides) - 1
    target_index = max(0, min(request.new_index, max_index))

    if slide.index == target_index:
        return absolute_mcp_result_links(
            api_request,
            {
                "slide": slide.model_dump(),
                "edit_path": f"/presentation?id={presentation.id}",
            },
        )

    old_index = slide.index
    slide.index = target_index
    sql_session.add(slide)

    # Shift other slides
    for s in slides:
        if s.id == slide.id:
            continue
        if old_index < target_index and old_index <= getattr(s, 'index', -1) <= target_index:
            s.index -= 1
            sql_session.add(s)
        elif target_index <= getattr(s, 'index', -1) < old_index:
            s.index += 1
            sql_session.add(s)

    await sql_session.commit()

    return absolute_mcp_result_links(
        api_request,
        {
            "slide": slide.model_dump(),
            "edit_path": f"/presentation?id={presentation.id}",
        },
    )
