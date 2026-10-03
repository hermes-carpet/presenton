import pytest

import mcp_server
from mcp_canvas import MCP_CANVAS_TOOL_NAMES


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
        ("standard", {"edit_slide", "create_slide"}, {"edit_slide_html", "update_slide_html"}),
        ("smart", {"edit_slide_html", "update_slide_html"}, {"`edit_slide`", "create_slide"}),
        ("both", {"edit_slide", "create_slide", "edit_slide_html"}, set()),
    ],
)
def test_canvas_instructions_only_name_tools_enabled_for_mode(mode, present, absent):
    instructions = mcp_server.get_mcp_instructions(mode)

    assert "# Canvas Editing Workflow" in instructions
    assert "get_presentation_context" in instructions
    for tool in present:
        assert tool in instructions
    for tool in absent:
        assert tool not in instructions
