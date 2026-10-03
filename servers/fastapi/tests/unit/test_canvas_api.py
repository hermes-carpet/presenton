import asyncio
import json

import httpx
import httpx2
import pytest
from fastapi import FastAPI
from fastmcp import Client, FastMCP
from fastmcp.server.providers.openapi import MCPType, RouteMap
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import select

import mcp_canvas
from api.v1.ppt.endpoints.canvas import CANVAS_ROUTER
from models.sql.image_asset import ImageAsset
from models.sql.presentation import PresentationModel, PresentationVersion
from models.sql.slide import SlideModel
from services.canvas_tool_registry import get_canvas_tools
from services.chat.memory_layer import BLANK_SLIDE_LAYOUT_ID
from services.database import get_async_session

LAYOUT_ID = "hero"
BASE_URL = "http://presenton.test"
TOOLS = "/api/v1/ppt/canvas/presentation/{id}/tools/{tool}"

# Real Template V2 payload, so layout tools read the deck the way they do in the app.
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

VALID_SMART_HTML = (
    '<section data-slide-type="content" data-slide-title="Updated title" '
    'class="relative h-[720px] w-[1280px] overflow-hidden bg-white">'
    '<h2 class="text-5xl">Updated title</h2></section>'
)


class _Deck:
    """In-memory SQLite database plus an app serving only the canvas router."""

    def __init__(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_maker = async_sessionmaker(self.engine, expire_on_commit=False)
        self.app = FastAPI()
        self.app.include_router(CANVAS_ROUTER, prefix="/api/v1/ppt")

        async def session():
            async with self.session_maker() as sql_session:
                yield sql_session

        self.app.dependency_overrides[get_async_session] = session

    async def setup(self, *rows):
        async with self.engine.begin() as connection:
            for model in (PresentationModel, SlideModel, ImageAsset):
                await connection.run_sync(model.__table__.create)
        async with self.session_maker() as sql_session:
            sql_session.add_all(rows)
            await sql_session.commit()

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url=BASE_URL)

    async def slides(self, presentation_id):
        async with self.session_maker() as sql_session:
            result = await sql_session.scalars(
                select(SlideModel)
                .where(SlideModel.presentation == presentation_id)
                .order_by(SlideModel.index)
            )
            return list(result)

    async def presentation(self, presentation_id):
        async with self.session_maker() as sql_session:
            return await sql_session.get(PresentationModel, presentation_id)


def _run(rows, scenario):
    async def runner():
        deck = _Deck()
        await deck.setup(*rows)
        try:
            return await scenario(deck)
        finally:
            await deck.engine.dispose()

    return asyncio.run(runner())


def _standard_deck(n_slides: int = 0):
    presentation = PresentationModel(
        version=PresentationVersion.V2_STANDARD,
        content="deck",
        n_slides=n_slides,
        language="English",
        title="Standard deck",
        layout=TEMPLATE_LAYOUT,
        generation_mode="standard",
    )
    slides = [
        SlideModel(
            presentation=presentation.id,
            layout_group="canvas-test",
            layout=LAYOUT_ID,
            index=index,
            content={"main": {"title": f"Slide {index}"}},
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


async def _call_tool(deck, presentation, tool, arguments=None):
    async with deck.client() as client:
        return await client.post(
            TOOLS.format(id=presentation.id, tool=tool),
            json=arguments if arguments is not None else {},
        )


# --- context -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("factory", "deck_type", "present", "absent"),
    [
        (_standard_deck, "standard", {"addElement": "addElement", "saveSlide": "saveSlide"}, {"getSmartPresentationContext"}),
        (_smart_deck, "smart", {"saveSlide": "smartSaveSlide", "searchSlide": "searchSlide"}, {"addElement"}),
    ],
)
def test_context_lists_deck_tools_and_editing_guide(factory, deck_type, present, absent):
    presentation, slides = factory(2)

    async def scenario(deck):
        async with deck.client() as client:
            return await client.get(f"/api/v1/ppt/canvas/presentation/{presentation.id}/context")

    response = _run([presentation, *slides], scenario)

    assert response.status_code == 200
    body = response.json()
    assert body["generation_mode"] == deck_type
    assert [(slide["index"], slide["id"]) for slide in body["slides"]] == [
        (0, str(slides[0].id)),
        (1, str(slides[1].id)),
    ]
    for tool_name, mcp_name in present.items():
        assert body["tools"][tool_name] == mcp_name
    assert not absent & set(body["tools"])
    assert "Slide Number Rules" in body["editing_guide"] or "0-based" in body["editing_guide"]
    assert body["edit_path"] == f"/presentation?id={presentation.id}"


# --- tool bridge ---------------------------------------------------------------


def test_get_slide_at_index_reads_the_slide():
    presentation, slides = _standard_deck(2)

    async def scenario(deck):
        return await _call_tool(deck, presentation, "getSlideAtIndex", {"index": 1, "includeFullContent": True})

    response = _run([presentation, *slides], scenario)

    assert response.status_code == 200
    assert "Slide 1" in json.dumps(response.json()["result"])


def test_add_new_slide_inserts_and_shifts_later_slides():
    presentation, slides = _standard_deck(2)

    async def scenario(deck):
        response = await _call_tool(deck, presentation, "addNewSlide", {"index": 1})
        return response, await deck.slides(presentation.id), await deck.presentation(presentation.id)

    response, stored, stored_presentation = _run([presentation, *slides], scenario)

    assert response.status_code == 200, response.text
    assert [slide.index for slide in stored] == [0, 1, 2]
    assert [slide.id for slide in stored][0] == slides[0].id
    assert stored[1].layout == BLANK_SLIDE_LAYOUT_ID
    assert stored[2].id == slides[1].id
    assert stored_presentation.n_slides == 3


def test_delete_slide_shifts_later_slides_and_decrements_count():
    presentation, slides = _standard_deck(3)

    async def scenario(deck):
        response = await _call_tool(deck, presentation, "deleteSlide", {"index": 0})
        return response, await deck.slides(presentation.id), await deck.presentation(presentation.id)

    response, stored, stored_presentation = _run([presentation, *slides], scenario)

    assert response.status_code == 200, response.text
    assert [(slide.id, slide.index) for slide in stored] == [(slides[1].id, 0), (slides[2].id, 1)]
    assert stored_presentation.n_slides == 2


def test_delete_last_slide_leaves_blank_fallback():
    presentation, slides = _standard_deck(1)

    async def scenario(deck):
        response = await _call_tool(deck, presentation, "deleteSlide", {"index": 0})
        return response, await deck.slides(presentation.id)

    response, stored = _run([presentation, *slides], scenario)

    assert response.status_code == 200
    (fallback,) = stored
    assert fallback.id != slides[0].id
    assert fallback.layout == BLANK_SLIDE_LAYOUT_ID
    assert response.json()["result"]["blank_fallback"] is True


def test_smart_save_slide_saves_html():
    presentation, slides = _smart_deck(1)

    async def scenario(deck):
        response = await _call_tool(
            deck,
            presentation,
            "smartSaveSlide",
            {"html": VALID_SMART_HTML, "index": 0, "replaceOldSlideAtIndex": True, "speakerNote": None},
        )
        return response, await deck.slides(presentation.id)

    response, (stored,) = _run([presentation, *slides], scenario)

    assert response.status_code == 200, response.text
    assert response.json()["result"]["saved"] is True
    assert "Updated title" in stored.html_content


def test_tool_failure_returns_assistant_recovery_guidance():
    presentation, slides = _standard_deck(1)

    async def scenario(deck):
        response = await _call_tool(deck, presentation, "deleteSlide", {"index": "first"})
        return response, await deck.slides(presentation.id)

    response, stored = _run([presentation, *slides], scenario)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["ok"] is False
    assert detail["tool"] == "deleteSlide"
    assert "recovery" in detail
    assert [slide.id for slide in stored] == [slides[0].id]


@pytest.mark.parametrize(
    ("factory", "tool"),
    [(_smart_deck, "addElement"), (_smart_deck, "saveSlide"), (_standard_deck, "smartSaveSlide")],
)
def test_tool_for_other_deck_type_is_rejected(factory, tool):
    presentation, slides = factory(1)

    async def scenario(deck):
        return await _call_tool(deck, presentation, tool, {"index": 0})

    response = _run([presentation, *slides], scenario)

    assert response.status_code == 400
    assert "get_presentation_context" in response.json()["detail"]


def test_unknown_presentation_returns_404():
    presentation, _ = _standard_deck()
    other, _ = _standard_deck()

    async def scenario(deck):
        return await _call_tool(deck, other, "getSlideAtIndex", {"index": 0})

    assert _run([presentation], scenario).status_code == 404


# --- reorder -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("moved", "new_index", "expected_order"),
    [
        (0, 2, [1, 2, 0]),
        (2, 0, [2, 0, 1]),
        (0, 1, [1, 0, 2]),
        (1, 99, [0, 2, 1]),
        (1, -3, [1, 0, 2]),
        (1, 1, [0, 1, 2]),
    ],
)
def test_reorder_slide_shifts_other_slides(moved, new_index, expected_order):
    presentation, slides = _standard_deck(3)

    async def scenario(deck):
        async with deck.client() as client:
            response = await client.patch(
                f"/api/v1/ppt/canvas/slide/{slides[moved].id}/reorder",
                json={"new_index": new_index},
            )
        return response, await deck.slides(presentation.id)

    response, stored = _run([presentation, *slides], scenario)

    assert response.status_code == 200
    assert [slide.id for slide in stored] == [slides[i].id for i in expected_order]
    assert [slide.index for slide in stored] == [0, 1, 2]
    assert response.json()["slide"]["index"] == expected_order.index(moved)


# --- registry and MCP end to end -------------------------------------------------


def test_registry_covers_every_assistant_tool_once():
    from services.canvas_tool_registry import _chat_tool_definitions

    for deck_type in ("standard", "smart"):
        expected = set(_chat_tool_definitions(deck_type))
        exposed = [tool.tool_name for tool in get_canvas_tools() if deck_type in tool.deck_types]
        assert sorted(exposed) == sorted(expected)

    mcp_names = [tool.mcp_name for tool in get_canvas_tools()]
    assert len(mcp_names) == len(set(mcp_names))
    for tool in get_canvas_tools():
        assert "$ref" not in json.dumps(tool.input_schema), tool.mcp_name


def _mcp_server(deck):
    client = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=deck.app), base_url=BASE_URL)
    server = FastMCP.from_openapi(
        openapi_spec=deck.app.openapi(),
        client=client,
        route_maps=[*mcp_canvas.get_canvas_route_maps("both"), RouteMap(mcp_type=MCPType.EXCLUDE)],
        mcp_names=mcp_canvas.MCP_CANVAS_TOOL_NAMES,
    )
    return server, client


def test_mcp_tools_edit_a_deck_end_to_end():
    presentation, slides = _standard_deck(2)

    async def scenario(deck):
        server, http_client = _mcp_server(deck)
        try:
            async with Client(server) as mcp:
                tools = {tool.name: tool for tool in await mcp.list_tools()}
                layouts = await mcp.call_tool(
                    "getAvailableLayouts", {"presentation_id": str(presentation.id)}
                )
                deleted = await mcp.call_tool(
                    "deleteSlide", {"presentation_id": str(presentation.id), "index": 0}
                )
                failed = await mcp.call_tool(
                    "deleteSlide",
                    {"presentation_id": str(presentation.id), "index": 5},
                    raise_on_error=False,
                )
                rejected = await mcp.call_tool(
                    "smartSaveSlide",
                    {"presentation_id": str(presentation.id), "html": "<p></p>", "index": 0,
                     "replaceOldSlideAtIndex": True, "speakerNote": None},
                    raise_on_error=False,
                )
        finally:
            await http_client.aclose()
        return tools, layouts, deleted, failed, rejected, await deck.slides(presentation.id)

    tools, layouts, deleted, failed, rejected, stored = _run([presentation, *slides], scenario)

    assert mcp_canvas.get_canvas_tool_names("both") == set(tools)
    assert set(tools["addElement"].input_schema["properties"]) >= {"presentation_id", "index", "element"}
    assert "hero" in json.dumps(layouts.structured_content)
    assert deleted.structured_content["result"]["deleted"] is True
    assert [slide.id for slide in stored] == [slides[1].id]
    assert "No slide found" in json.dumps(failed.structured_content or failed.content[0].text)
    assert rejected.is_error
    assert "get_presentation_context" in rejected.content[0].text
