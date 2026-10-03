import asyncio
import uuid
from typing import Any

from fastapi import HTTPException
import pytest
from sqlalchemy.dialects import sqlite

from api.v1.ppt.endpoints import canvas as canvas_endpoint
from api.v1.ppt.endpoints import template as template_endpoint
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel

class _RowsResult:
    def __init__(self, values=None):
        self.values = values or []

    def all(self):
        return self.values

    def scalars(self):
        return self

class _CapturingAsyncSession:
    def __init__(self, values=None):
        self.executed_statement: Any = None
        self.values = values or []
        self.added = []
        self.committed = False

    async def execute(self, statement: Any):
        self.executed_statement = statement
        # For select queries, filter values matching the model
        model_name = ""
        if "from slides" in str(statement).lower():
            model_name = "slidemodel"
        elif "from presentations" in str(statement).lower():
            model_name = "presentationmodel"

        filtered_values = [v for v in self.values if model_name in str(v.__class__.__name__).lower()] if model_name else self.values
        return _RowsResult(filtered_values)

    async def get(self, model, id):
        for v in self.values:
            if isinstance(v, model) and v.id == id:
                return v
        return None

    def add(self, obj):
        self.added.append(obj)

    def add_all(self, objs):
        self.added.extend(objs)

    async def commit(self):
        self.committed = True

def _compile_statement(statement: Any) -> str:
    return str(
        statement.compile(
            dialect=sqlite.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

def test_get_template_schema():
    presentation_id = uuid.uuid4()
    layout_id = "test-layout"

    presentation = PresentationModel(
        id=presentation_id,
        n_slides=0,
        layout={
            "name": "test",
            "ordered": False,
            "icon_type": "flat",
            "slides": [
                {
                    "id": layout_id,
                    "name": "layout",
                    "description": "",
                    "json_schema": {"type": "object", "properties": {"title": {"type": "string"}}},
                }
            ]
        }
    )

    session = _CapturingAsyncSession([presentation])

    response = asyncio.run(
        template_endpoint.get_template_schema(
            presentation_id=presentation_id,
            layout_id=layout_id,
            sql_session=session,
        )
    )

    assert response["type"] == "object"
    assert response["properties"] == {"title": {"type": "string"}}

def test_canvas_validate_json():
    presentation_id = uuid.uuid4()
    layout_id = "test-layout"

    presentation = PresentationModel(
        id=presentation_id,
        n_slides=0,
        layout={
            "name": "test",
            "ordered": False,
            "icon_type": "flat",
            "slides": [
                {
                    "id": layout_id,
                    "name": "layout",
                    "description": "",
                    "json_schema": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
                }
            ]
        }
    )

    session = _CapturingAsyncSession([presentation])

    # Valid JSON
    response = asyncio.run(
        canvas_endpoint.canvas_validate_json(
            request=canvas_endpoint.ValidateJsonRequest(
                presentation_id=presentation_id,
                layout_id=layout_id,
                content={"title": "Hello"}
            ),
            sql_session=session,
        )
    )

    assert response.valid is True
    assert len(response.errors) == 0

    # Invalid JSON
    response = asyncio.run(
        canvas_endpoint.canvas_validate_json(
            request=canvas_endpoint.ValidateJsonRequest(
                presentation_id=presentation_id,
                layout_id=layout_id,
                content={"not_title": "Hello"}
            ),
            sql_session=session,
        )
    )

    assert response.valid is False
    assert len(response.errors) > 0


def test_canvas_create_slide_bounds():
    presentation_id = uuid.uuid4()
    layout_id = "test-layout"

    presentation = PresentationModel(
        id=presentation_id,
        layout={
            "name": "test",
            "ordered": False,
            "icon_type": "flat",
            "layouts": [
                {
                    "id": layout_id,
                    "name": "layout",
                    "description": "",
                    "json_schema": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
                }
            ]
        }
    )

    session = _CapturingAsyncSession([presentation])

    request = type('Request', (), {'headers': {}})()

    # Exceed limit
    from constants.presentation import MAX_NUMBER_OF_SLIDES
    presentation.n_slides = MAX_NUMBER_OF_SLIDES
    slides = [SlideModel(id=uuid.uuid4(), presentation=presentation_id, index=i, layout_group="test", layout=layout_id, content={}) for i in range(MAX_NUMBER_OF_SLIDES)]
    session.values.extend(slides)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            canvas_endpoint.canvas_create_slide(
                request=canvas_endpoint.CreateSlideRequest(
                    presentation_id=presentation_id,
                    layout_id=layout_id,
                    content={"title": "Hello"}
                ),
                api_request=request,
                sql_session=session,
            )
        )
    assert "Cannot exceed maximum" in exc.value.detail

def test_canvas_smart_deck_rejection():
    presentation_id = uuid.uuid4()

    presentation = PresentationModel(
        id=presentation_id,
        generation_mode="smart",
        layout=None,
    )

    session = _CapturingAsyncSession([presentation])
    request = type('Request', (), {'headers': {}})()

    # get_template_schema
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            template_endpoint.get_template_schema(
                presentation_id=presentation_id,
                layout_id="any",
                sql_session=session,
            )
        )
    assert exc.value.status_code == 400

    # validate_json
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            canvas_endpoint.canvas_validate_json(
                request=canvas_endpoint.ValidateJsonRequest(
                    presentation_id=presentation_id,
                    layout_id="any",
                    content={}
                ),
                sql_session=session,
            )
        )
    assert exc.value.status_code == 400


def test_canvas_create_slide_success():
    presentation_id = uuid.uuid4()
    layout_id = "test-layout"

    presentation = PresentationModel(
        id=presentation_id,
        layout={
            "name": "test",
            "ordered": False,
            "icon_type": "flat",
            "layouts": [
                {
                    "id": layout_id,
                    "name": "layout",
                    "description": "",
                    "json_schema": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
                }
            ]
        }
    )

    session = _CapturingAsyncSession([presentation])
    request = type('Request', (), {'headers': {}})()

    response = asyncio.run(
        canvas_endpoint.canvas_create_slide(
            request=canvas_endpoint.CreateSlideRequest(
                presentation_id=presentation_id,
                layout_id=layout_id,
                content={"title": "Hello"}
            ),
            api_request=request,
            sql_session=session,
        )
    )

    assert response["slide"]["content"] == {"title": "Hello"}
    # Verify slide was added to session
    assert len(session.added) >= 1
    added_slide = next(s for s in session.added if isinstance(s, SlideModel))
    assert added_slide.layout == layout_id

def test_canvas_update_slide():
    presentation_id = uuid.uuid4()
    slide_id = uuid.uuid4()
    layout_id = "test-layout"

    presentation = PresentationModel(
        id=presentation_id,
        layout={
            "name": "test",
            "ordered": False,
            "icon_type": "flat",
            "layouts": [
                {
                    "id": layout_id,
                    "name": "layout",
                    "description": "",
                    "json_schema": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
                }
            ]
        }
    )

    slide = SlideModel(
        id=slide_id,
        presentation=presentation_id,
        layout_group="test",
        layout=layout_id,
        index=0,
        content={"title": "Old"},
    )

    session = _CapturingAsyncSession([presentation, slide])
    request = type('Request', (), {'headers': {}})()

    response = asyncio.run(
        canvas_endpoint.canvas_update_slide(
            slide_id=slide_id,
            request=canvas_endpoint.UpdateSlideRequest(
                layout_id=layout_id,
                content={"title": "New"}
            ),
            api_request=request,
            sql_session=session,
        )
    )

    assert response["slide"]["content"] == {"title": "New"}

def test_canvas_delete_slide():
    presentation_id = uuid.uuid4()
    slide_id = uuid.uuid4()

    presentation = PresentationModel(
        id=presentation_id,
        n_slides=1,
    )

    slide = SlideModel(
        id=slide_id,
        presentation=presentation_id,
        layout_group="test",
        layout="test-layout",
        index=0,
        content={},
    )

    async def mock_delete(x):
        pass

    session = _CapturingAsyncSession([presentation, slide])
    session.delete = mock_delete
    request = type('Request', (), {'headers': {}})()

    response = asyncio.run(
        canvas_endpoint.canvas_delete_slide(
            slide_id=slide_id,
            api_request=request,
            sql_session=session,
        )
    )

    assert response["success"] is True

def test_canvas_reorder_slide():
    presentation_id = uuid.uuid4()
    slide_id = uuid.uuid4()

    presentation = PresentationModel(
        id=presentation_id,
        n_slides=2,
    )

    slide = SlideModel(
        id=slide_id,
        presentation=presentation_id,
        layout_group="test",
        layout="test-layout",
        index=0,
        content={},
    )

    slide2 = SlideModel(
        id=uuid.uuid4(),
        presentation=presentation_id,
        layout_group="test",
        layout="test-layout",
        index=1,
        content={},
    )

    session = _CapturingAsyncSession([presentation, slide, slide2])
    request = type('Request', (), {'headers': {}})()

    response = asyncio.run(
        canvas_endpoint.canvas_reorder_slide(
            slide_id=slide_id,
            request=canvas_endpoint.ReorderSlideRequest(new_index=1),
            api_request=request,
            sql_session=session,
    )
    )

    # We also need to check the modified slide object directly because the response may just serialize the initial state if the ref gets weird or if index wasn't set correctly on the dictionary representation
    # Actually, in our reorder logic, we just modify the slide in place and return it.
    # Let's see if the session caught the modification.
    modified_slide = next((s for s in session.values if getattr(s, "id", None) == slide_id), slide)
    assert modified_slide.index == 1
    assert response["slide"]["index"] == 1
