import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import mcp_canvas
import mcp_server
from mcp_canvas import MCP_CANVAS_TOOL_NAMES, get_canvas_tool_names


def test_canvas_tool_names_are_registered_with_mcp_server():
    for operation_id, tool_name in MCP_CANVAS_TOOL_NAMES.items():
        assert mcp_server.MCP_TOOL_NAMES[operation_id] == tool_name


def test_canvas_operation_ids_exist_in_openapi_spec():
    operation_ids = {
        operation["operationId"]
        for path in mcp_server.openapi_spec["paths"].values()
        for operation in path.values()
    }
    assert set(MCP_CANVAS_TOOL_NAMES) <= operation_ids


@pytest.mark.parametrize(
    ("mode", "present", "absent"),
    [
        ("standard", {"addElement", "saveSlide", "getTemplateSummary"}, {"smartSaveSlide", "getSmartPresentationContext"}),
        ("smart", {"smartSaveSlide", "getSmartPresentationContext"}, {"addElement", "saveSlide"}),
        ("both", {"addElement", "saveSlide", "smartSaveSlide"}, set()),
    ],
)
def test_canvas_tools_follow_generation_mode(mode, present, absent):
    names = get_canvas_tool_names(mode)

    assert {"get_presentation_context", "reorder_slide", "deleteSlide", "searchSlide"} <= names
    assert present <= names
    assert not absent & names


@pytest.mark.parametrize("mode", ["standard", "smart", "both"])
def test_canvas_instructions_point_to_context_tool(mode):
    instructions = mcp_server.get_mcp_instructions(mode)

    assert "# Editing existing presentations" in instructions
    assert "get_presentation_context" in instructions


def test_spec_tools_match_tool_registry():
    from services.canvas_tool_registry import DECK_TYPES, DECK_TYPES_EXTENSION, get_canvas_tools

    assert mcp_canvas.DECK_TYPES == DECK_TYPES
    assert mcp_canvas.DECK_TYPES_EXTENSION == DECK_TYPES_EXTENSION
    assert sorted(
        ((tool.operation_id, tool.mcp_name, tool.path, tool.deck_types) for tool in mcp_canvas._SPEC_TOOLS),
        key=lambda item: item[0],
    ) == sorted(
        (
            (tool.operation_id, tool.mcp_name, mcp_canvas.CANVAS_PREFIX + tool.path, tool.deck_types)
            for tool in get_canvas_tools()
        ),
        key=lambda item: item[0],
    )


def test_mcp_server_import_stays_light(tmp_path):
    # The MCP process must not import the chat stack: it loads the ML libraries,
    # and services.temp_file_service empties TEMP_DIRECTORY on import, which
    # races FastAPI's own cleanup at startup and deletes its in-flight files.
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    sentinel = temp_dir / "sentinel"
    sentinel.write_text("keep")
    heavy = ["services.chat", "services.temp_file_service", "fastembed", "onnxruntime", "numpy"]
    script = (
        "import sys, mcp_server\n"
        f"print([m for m in {heavy!r} if m in sys.modules])\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(mcp_server.__file__).parent,
        env={**os.environ, "TEMP_DIRECTORY": str(temp_dir)},
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "[]"
    assert sentinel.exists()


def _write_spec(tmp_path, monkeypatch, paths):
    spec_path = tmp_path / "openai_spec.json"
    spec_path.write_text(json.dumps({"paths": paths}))
    monkeypatch.setattr(mcp_canvas, "OPENAPI_SPEC_PATH", spec_path)


def _tool_path(name):
    return f"{mcp_canvas.CANVAS_PREFIX}/presentation/{{presentation_id}}/tools/{name}"


def test_spec_parser_skips_path_level_keys(tmp_path, monkeypatch):
    _write_spec(tmp_path, monkeypatch, {
        _tool_path("saveSlide"): {
            "parameters": [],
            "post": {"operationId": "canvas_tool_saveSlide", "x-presenton-deck-types": ["standard"]},
        },
    })

    (tool,) = mcp_canvas._load_spec_tools()
    assert (tool.mcp_name, tool.deck_types) == ("saveSlide", frozenset({"standard"}))


@pytest.mark.parametrize(
    "paths",
    [
        {_tool_path("other"): {"post": {"operationId": "canvas_tool_saveSlide", "x-presenton-deck-types": ["standard"]}}},
        {_tool_path("saveSlide"): {"put": {"operationId": "canvas_tool_saveSlide", "x-presenton-deck-types": ["standard"]}}},
        {_tool_path("saveSlide"): {"post": {"operationId": "canvas_tool_saveSlide"}}},
        {_tool_path("saveSlide"): {"post": {"operationId": "canvas_tool_saveSlide", "x-presenton-deck-types": ["slides"]}}},
        {
            _tool_path("saveSlide"): {
                "post": {"operationId": "canvas_tool_saveSlide", "x-presenton-deck-types": ["standard"]},
                "patch": {"operationId": "canvas_tool_saveSlide", "x-presenton-deck-types": ["standard"]},
            }
        },
        {"/api/v1/ppt/other": {"get": {"operationId": "something_else"}}},
    ],
)
def test_spec_parser_rejects_bad_canvas_routes(tmp_path, monkeypatch, paths):
    _write_spec(tmp_path, monkeypatch, paths)

    with pytest.raises(RuntimeError, match="regenerate the spec"):
        mcp_canvas._load_spec_tools()
