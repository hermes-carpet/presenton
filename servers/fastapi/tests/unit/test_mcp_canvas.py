import pytest

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
