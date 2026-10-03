import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import select

from api.v1.ppt.endpoints import canvas as canvas_endpoint
from constants.presentation import MAX_NUMBER_OF_SLIDES
from models.sql.image_asset import ImageAsset
from models.sql.presentation import PresentationModel, PresentationVersion
from models.sql.slide import SlideModel
from services.chat.memory_layer import BLANK_SLIDE_LAYOUT_ID

LAYOUT_ID = "hero"
IMAGE_URL = "https://example.com/generated.png"
API_REQUEST = SimpleNamespace(headers={})

# Real Template V2 payload: the canvas endpoints derive the JSON schema from the
# components, so this exercises _get_presentation_stream_layout's template branch.
TEMPLATE_LAYOUT = {
    "name": "canvas-test",
    "layouts": [
        {
            "id": LAYOUT_ID,
            "name": "Hero",
            "description": "Title with a photo",
            "components": [
                {
                    "id": "main",
                    "elements": [
                        {
                            "type": "text",
                            "name": "title",
                            "decorative": False,
                            "size": {"width": 400, "height": 50},
                        },
                        {
                            "type": "image",
                            "name": "photo",
                            "decorative": False,
                            "size": {"width": 400, "height": 200},
                        },
                    ],
                }
            ],
        }
    ],
}


def _content(title: str = "Hello", prompt: str = "a cat") -> dict:
    return {"main": {"title": title, "photo": {"image_prompt": prompt}}}


def _ui_element(ui: dict, name: str) -> dict:
    elements = ui["components"][0]["elements"]
    return next(element for element in elements if element["name"] == name)


class _Db:
    """In-memory SQLite database with the tables the canvas endpoints touch."""

    def __init__(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_maker = async_sessionmaker(self.engine, expire_on_commit=False)

    async def setup(self):
        async with self.engine.begin() as connection:
            await connection.run_sync(PresentationModel.__table__.create)
            await connection.run_sync(SlideModel.__table__.create)
            await connection.run_sync(ImageAsset.__table__.create)

    async def add(self, *rows):
        async with self.session_maker() as session:
            session.add_all(rows)
            await session.commit()

    async def slides(self, presentation_id):
        async with self.session_maker() as session:
            result = await session.scalars(
                select(SlideModel)
                .where(SlideModel.presentation == presentation_id)
                .order_by(SlideModel.index)
            )
            return list(result)

    async def presentation(self, presentation_id):
        async with self.session_maker() as session:
            return await session.get(PresentationModel, presentation_id)

    async def call(self, endpoint, **kwargs):
        async with self.session_maker() as session:
            return await endpoint(sql_session=session, **kwargs)


def _run(scenario):
    async def runner():
        db = _Db()
        await db.setup()
        try:
            return await scenario(db)
        finally:
            await db.engine.dispose()

    return asyncio.run(runner())


def _standard_deck(n_slides: int = 0):
    presentation = PresentationModel(
        version=PresentationVersion.V2_STANDARD,
        content="deck",
        n_slides=n_slides,
        language="English",
        layout=TEMPLATE_LAYOUT,
        generation_mode="standard",
    )
    slides = [
        SlideModel(
            presentation=presentation.id,
            layout_group="canvas-test",
            layout=LAYOUT_ID,
            index=index,
            content=_content(f"Slide {index}"),
        )
        for index in range(n_slides)
    ]
    return presentation, slides


def _smart_deck(n_slides: int = 1):
    presentation = PresentationModel(
        version=PresentationVersion.V2_STANDARD,
        content="deck",
        n_slides=n_slides,
        language="English",
        layout=None,
        generation_mode="smart",
    )
    slides = [
        SlideModel(
            presentation=presentation.id,
            layout_group="smart-html",
            layout="smart-html",
            index=index,
            content={"title": f"Slide {index}"},
            html_content=f"<section>Slide {index}</section>",
        )
        for index in range(n_slides)
    ]
    return presentation, slides


@pytest.fixture
def fake_assets(monkeypatch):
    """Simulate image fetching by writing an image_url next to each prompt."""

    async def fake_process_slide(_service, slide, **_kwargs):
        slide.content["main"]["photo"]["image_url"] = IMAGE_URL
        return []

    async def fake_process_old_and_new(_service, _old, new_content, **_kwargs):
        new_content["main"]["photo"]["image_url"] = IMAGE_URL
        return []

    monkeypatch.setattr(canvas_endpoint, "process_slide_and_fetch_assets", fake_process_slide)
    monkeypatch.setattr(
        canvas_endpoint,
        "process_old_and_new_slides_and_fetch_assets",
        fake_process_old_and_new,
    )


@pytest.fixture
def fake_memory(monkeypatch):
    calls = []

    async def retrieve_context(_presentation_id, _query):
        return ""

    async def store_slide_edit(**kwargs):
        calls.append(kwargs)

    memory = canvas_endpoint.MEM0_PRESENTATION_MEMORY_SERVICE
    monkeypatch.setattr(memory, "retrieve_context", retrieve_context)
    monkeypatch.setattr(memory, "store_slide_edit", store_slide_edit)
    return calls


# --- context / schema / validation -------------------------------------------


def test_context_lists_template_layouts_and_slides():
    presentation, slides = _standard_deck(2)

    async def scenario(db):
        await db.add(presentation, *slides)
        return await db.call(
            canvas_endpoint.get_canvas_context,
            presentation_id=presentation.id,
            request=API_REQUEST,
        )

    response = _run(scenario)

    assert response.generation_mode == "standard"
    assert [(layout.id, layout.name) for layout in response.layouts] == [(LAYOUT_ID, "Hero")]
    assert [slide.index for slide in response.presentation.slides] == [0, 1]


def test_slide_schema_is_derived_from_template_components():
    presentation, _ = _standard_deck()

    async def scenario(db):
        await db.add(presentation)
        return await db.call(
            canvas_endpoint.canvas_get_slide_schema,
            presentation_id=presentation.id,
            layout_id=LAYOUT_ID,
        )

    schema = _run(scenario)

    main = schema["properties"]["main"]["properties"]
    assert set(main) == {"title", "photo"}
    assert main["photo"]["required"] == ["image_prompt"]


def test_slide_schema_unknown_layout_returns_404():
    presentation, _ = _standard_deck()

    async def scenario(db):
        await db.add(presentation)
        return await db.call(
            canvas_endpoint.canvas_get_slide_schema,
            presentation_id=presentation.id,
            layout_id="missing",
        )

    with pytest.raises(HTTPException) as exc:
        _run(scenario)
    assert exc.value.status_code == 404


def test_validate_json_reports_schema_errors():
    presentation, _ = _standard_deck()

    def request(content):
        return canvas_endpoint.ValidateJsonRequest(
            presentation_id=presentation.id, layout_id=LAYOUT_ID, content=content
        )

    async def scenario(db):
        await db.add(presentation)
        valid = await db.call(canvas_endpoint.canvas_validate_json, request=request(_content()))
        invalid = await db.call(canvas_endpoint.canvas_validate_json, request=request({"main": {}}))
        return valid, invalid

    valid, invalid = _run(scenario)

    assert valid.valid is True and valid.errors == []
    assert invalid.valid is False and invalid.errors


# --- create ------------------------------------------------------------------


def _create(db, presentation, index=None, content=None):
    return db.call(
        canvas_endpoint.canvas_create_slide,
        request=canvas_endpoint.CreateSlideRequest(
            presentation_id=presentation.id,
            layout_id=LAYOUT_ID,
            content=content or _content("New"),
            index=index,
        ),
        api_request=API_REQUEST,
    )


@pytest.mark.parametrize(
    ("requested_index", "expected_index"),
    [(None, 2), (1, 1), (0, 0), (-5, 0), (99, 2)],
)
def test_create_slide_clamps_index_and_shifts_later_slides(
    fake_assets, requested_index, expected_index
):
    presentation, slides = _standard_deck(2)

    async def scenario(db):
        await db.add(presentation, *slides)
        response = await _create(db, presentation, index=requested_index)
        return response, await db.slides(presentation.id), await db.presentation(presentation.id)

    response, stored, stored_presentation = _run(scenario)

    titles = [slide.content["main"]["title"] for slide in stored]
    expected_titles = ["Slide 0", "Slide 1"]
    expected_titles.insert(expected_index, "New")
    assert titles == expected_titles
    assert [slide.index for slide in stored] == [0, 1, 2]
    assert response["slide"]["index"] == expected_index
    assert stored_presentation.n_slides == 3


def test_create_slide_ui_contains_fetched_image(fake_assets):
    presentation, _ = _standard_deck()

    async def scenario(db):
        await db.add(presentation)
        await _create(db, presentation)
        return await db.slides(presentation.id)

    (created,) = _run(scenario)

    assert created.content["main"]["photo"]["image_url"] == IMAGE_URL
    assert _ui_element(created.ui, "photo")["data"] == IMAGE_URL


def test_create_slide_rejects_invalid_content():
    presentation, _ = _standard_deck()

    async def scenario(db):
        await db.add(presentation)
        return await _create(db, presentation, content={"main": {}})

    with pytest.raises(HTTPException) as exc:
        _run(scenario)
    assert exc.value.status_code == 400
    assert exc.value.detail["message"] == "Invalid JSON content"


def test_create_slide_respects_max_slides(fake_assets):
    presentation, slides = _standard_deck(MAX_NUMBER_OF_SLIDES)

    async def scenario(db):
        await db.add(presentation, *slides)
        return await _create(db, presentation)

    with pytest.raises(HTTPException) as exc:
        _run(scenario)
    assert exc.value.status_code == 400
    assert "maximum slide limit" in exc.value.detail


# --- update / edit -----------------------------------------------------------


def test_update_slide_rebuilds_ui_with_fetched_image(fake_assets):
    presentation, slides = _standard_deck(1)

    async def scenario(db):
        await db.add(presentation, *slides)
        response = await db.call(
            canvas_endpoint.canvas_update_slide,
            slide_id=slides[0].id,
            request=canvas_endpoint.UpdateSlideRequest(
                layout_id=LAYOUT_ID, content=_content("Updated", "a dog")
            ),
            api_request=API_REQUEST,
        )
        return response, await db.slides(presentation.id)

    response, (stored,) = _run(scenario)

    assert response["slide"]["content"]["main"]["title"] == "Updated"
    assert stored.content["main"]["title"] == "Updated"
    assert _ui_element(stored.ui, "photo")["data"] == IMAGE_URL
    assert stored.index == 0


def test_edit_slide_applies_llm_content(fake_assets, fake_memory, monkeypatch):
    presentation, slides = _standard_deck(1)
    seen = {}

    async def fake_layout(prompt, presentation_layout, _slide, _memory):
        seen["layout_prompt"] = prompt
        return next(layout for layout in presentation_layout.slides if layout.id == LAYOUT_ID)

    async def fake_content(prompt, _slide, language, _slide_layout, *_args):
        seen["content_prompt"] = prompt
        seen["language"] = language
        return _content("From LLM", "a bird")

    monkeypatch.setattr(canvas_endpoint, "get_slide_layout_from_prompt", fake_layout)
    monkeypatch.setattr(canvas_endpoint, "get_edited_slide_content", fake_content)

    async def scenario(db):
        await db.add(presentation, *slides)
        response = await db.call(
            canvas_endpoint.canvas_edit_slide,
            slide_id=slides[0].id,
            request=canvas_endpoint.EditSlideRequest(prompt="make it about birds"),
            api_request=API_REQUEST,
        )
        return response, await db.slides(presentation.id)

    response, (stored,) = _run(scenario)

    assert seen == {
        "layout_prompt": "make it about birds",
        "content_prompt": "make it about birds",
        "language": "English",
    }
    assert stored.content["main"]["title"] == "From LLM"
    assert _ui_element(stored.ui, "photo")["data"] == IMAGE_URL
    assert response["edit_path"].endswith(f"/presentation?id={presentation.id}")
    assert fake_memory[0]["edit_prompt"] == "make it about birds"


def test_edit_slide_html_applies_llm_html(fake_memory, monkeypatch):
    presentation, slides = _smart_deck(1)

    async def fake_html(prompt, html, _memory):
        return f"<section>{prompt}|{html}</section>"

    monkeypatch.setattr(canvas_endpoint, "get_edited_slide_html", fake_html)

    async def scenario(db):
        await db.add(presentation, *slides)
        await db.call(
            canvas_endpoint.canvas_edit_slide_html,
            slide_id=slides[0].id,
            request=canvas_endpoint.EditSlideHtmlRequest(prompt="bigger"),
            api_request=API_REQUEST,
        )
        return await db.slides(presentation.id)

    (stored,) = _run(scenario)

    assert stored.html_content == "<section>bigger|<section>Slide 0</section></section>"
    assert fake_memory[0]["edit_prompt"] == "bigger"


def test_update_slide_html_saves_html_on_smart_deck():
    presentation, slides = _smart_deck(1)

    async def scenario(db):
        await db.add(presentation, *slides)
        await db.call(
            canvas_endpoint.canvas_update_slide_html,
            slide_id=slides[0].id,
            request=canvas_endpoint.UpdateSlideHtmlRequest(html="<section>new</section>"),
            api_request=API_REQUEST,
        )
        return await db.slides(presentation.id)

    (stored,) = _run(scenario)
    assert stored.html_content == "<section>new</section>"


# --- delete ------------------------------------------------------------------


def _delete_and_reload(presentation, slides, slide_to_delete):
    async def scenario(db):
        await db.add(presentation, *slides)
        response = await db.call(
            canvas_endpoint.canvas_delete_slide,
            slide_id=slide_to_delete.id,
            api_request=API_REQUEST,
        )
        return response, await db.slides(presentation.id), await db.presentation(presentation.id)

    return _run(scenario)


def test_delete_slide_shifts_later_slides_and_decrements_count():
    presentation, slides = _standard_deck(3)

    response, stored, stored_presentation = _delete_and_reload(presentation, slides, slides[0])

    assert response["deleted_slide_id"] == str(slides[0].id)
    assert response["blank_fallback_slide_id"] is None
    assert [(slide.id, slide.index) for slide in stored] == [
        (slides[1].id, 0),
        (slides[2].id, 1),
    ]
    assert stored_presentation.n_slides == 2
    assert response["n_slides"] == 2


def test_delete_last_standard_slide_leaves_blank_fallback():
    presentation, slides = _standard_deck(1)

    response, stored, stored_presentation = _delete_and_reload(presentation, slides, slides[0])

    (fallback,) = stored
    assert fallback.id != slides[0].id
    assert fallback.index == 0
    assert fallback.layout == BLANK_SLIDE_LAYOUT_ID
    assert fallback.ui is not None
    assert response["blank_fallback_slide_id"] == str(fallback.id)
    assert stored_presentation.n_slides == 1


def test_delete_last_smart_slide_leaves_blank_html_fallback():
    presentation, slides = _smart_deck(1)

    response, stored, stored_presentation = _delete_and_reload(presentation, slides, slides[0])

    (fallback,) = stored
    assert fallback.id != slides[0].id
    assert fallback.layout == "smart-html"
    assert "Untitled slide" in fallback.html_content
    assert response["blank_fallback_slide_id"] == str(fallback.id)
    assert stored_presentation.n_slides == 1


# --- reorder -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("moved", "new_index", "expected_order"),
    [
        (0, 2, [1, 2, 0]),
        (2, 0, [2, 0, 1]),
        (0, 1, [1, 0, 2]),
        (1, 99, [0, 2, 1]),
        (1, -3, [1, 0, 2]),
    ],
)
def test_reorder_slide_shifts_other_slides(moved, new_index, expected_order):
    presentation, slides = _standard_deck(3)

    async def scenario(db):
        await db.add(presentation, *slides)
        response = await db.call(
            canvas_endpoint.canvas_reorder_slide,
            slide_id=slides[moved].id,
            request=canvas_endpoint.ReorderSlideRequest(new_index=new_index),
            api_request=API_REQUEST,
        )
        return response, await db.slides(presentation.id)

    response, stored = _run(scenario)

    assert [slide.id for slide in stored] == [slides[i].id for i in expected_order]
    assert [slide.index for slide in stored] == [0, 1, 2]
    assert response["slide"]["index"] == expected_order.index(moved)


# --- generation-mode guards --------------------------------------------------


def test_smart_deck_context_has_no_layouts():
    presentation, slides = _smart_deck(1)

    async def scenario(db):
        await db.add(presentation, *slides)
        return await db.call(
            canvas_endpoint.get_canvas_context,
            presentation_id=presentation.id,
            request=API_REQUEST,
        )

    response = _run(scenario)
    assert response.generation_mode == "smart"
    assert response.layouts == []
    assert len(response.presentation.slides) == 1


@pytest.mark.parametrize(
    "call",
    [
        lambda db, p, s: db.call(
            canvas_endpoint.canvas_get_slide_schema, presentation_id=p.id, layout_id="x"
        ),
        lambda db, p, s: db.call(
            canvas_endpoint.canvas_validate_json,
            request=canvas_endpoint.ValidateJsonRequest(
                presentation_id=p.id, layout_id="x", content={}
            ),
        ),
        lambda db, p, s: db.call(
            canvas_endpoint.canvas_create_slide,
            request=canvas_endpoint.CreateSlideRequest(
                presentation_id=p.id, layout_id="x", content={}
            ),
            api_request=API_REQUEST,
        ),
        lambda db, p, s: db.call(
            canvas_endpoint.canvas_update_slide,
            slide_id=s.id,
            request=canvas_endpoint.UpdateSlideRequest(layout_id="x", content={}),
            api_request=API_REQUEST,
        ),
        lambda db, p, s: db.call(
            canvas_endpoint.canvas_edit_slide,
            slide_id=s.id,
            request=canvas_endpoint.EditSlideRequest(prompt="x"),
            api_request=API_REQUEST,
        ),
    ],
    ids=["schema", "validate_json", "create", "update", "edit"],
)
def test_standard_endpoints_reject_smart_decks(fake_memory, call):
    presentation, slides = _smart_deck(1)

    async def scenario(db):
        await db.add(presentation, *slides)
        return await call(db, presentation, slides[0])

    with pytest.raises(HTTPException) as exc:
        _run(scenario)
    assert exc.value.status_code == 400


@pytest.mark.parametrize(
    "request_factory",
    [
        lambda s: (
            canvas_endpoint.canvas_update_slide_html,
            canvas_endpoint.UpdateSlideHtmlRequest(html="<p>new</p>"),
        ),
        lambda s: (
            canvas_endpoint.canvas_edit_slide_html,
            canvas_endpoint.EditSlideHtmlRequest(prompt="x", current_html="<p>new</p>"),
        ),
    ],
    ids=["update_html", "edit_html"],
)
def test_smart_html_endpoints_reject_standard_decks(fake_memory, monkeypatch, request_factory):
    presentation, slides = _standard_deck(1)

    async def fail_html(*_args):
        raise AssertionError("LLM must not be called for a Standard deck")

    monkeypatch.setattr(canvas_endpoint, "get_edited_slide_html", fail_html)

    async def scenario(db):
        await db.add(presentation, *slides)
        endpoint, request = request_factory(slides[0])
        try:
            await db.call(
                endpoint, slide_id=slides[0].id, request=request, api_request=API_REQUEST
            )
        except HTTPException as exc:
            return exc, await db.slides(presentation.id)
        raise AssertionError("expected HTTPException")

    exc, (stored,) = _run(scenario)

    assert exc.status_code == 400
    assert stored.id == slides[0].id
    assert stored.html_content is None
