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
        return _RowsResult(self.values)

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
