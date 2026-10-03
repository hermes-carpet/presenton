"""Canvas editing tools for the MCP server.

Kept out of mcp_server.py so upstream changes there merge cleanly; mcp_server.py
only calls the hooks below. See MODS.md.
"""

from fastmcp.server.providers.openapi import MCPType, RouteMap

from utils.get_env import PresentationGenerationMode

MCP_CANVAS_SHARED_ROUTE_MAPS = [
    RouteMap(
        methods=["GET"],
        pattern=r"^/api/v1/ppt/canvas/presentation/\{presentation_id\}/context$",
        mcp_type=MCPType.TOOL,
    ),
    RouteMap(
        methods=["DELETE"],
        pattern=r"^/api/v1/ppt/canvas/slide/\{slide_id\}$",
        mcp_type=MCPType.TOOL,
    ),
    RouteMap(
        methods=["PATCH"],
        pattern=r"^/api/v1/ppt/canvas/slide/\{slide_id\}/reorder$",
        mcp_type=MCPType.TOOL,
    ),
]

MCP_CANVAS_STANDARD_ROUTE_MAPS = [
    RouteMap(
        methods=["GET"],
        pattern=r"^/api/v1/ppt/canvas/schema$",
        mcp_type=MCPType.TOOL,
    ),
    RouteMap(
        methods=["POST"],
        pattern=r"^/api/v1/ppt/canvas/slide/\{slide_id\}/edit$",
        mcp_type=MCPType.TOOL,
    ),
    RouteMap(
        methods=["PATCH"],
        pattern=r"^/api/v1/ppt/canvas/slide/\{slide_id\}$",
        mcp_type=MCPType.TOOL,
    ),
    RouteMap(
        methods=["POST"],
        pattern=r"^/api/v1/ppt/canvas/validate-json$",
        mcp_type=MCPType.TOOL,
    ),
    RouteMap(
        methods=["POST"],
        pattern=r"^/api/v1/ppt/canvas/slide/create$",
        mcp_type=MCPType.TOOL,
    ),
]

MCP_CANVAS_SMART_ROUTE_MAPS = [
    RouteMap(
        methods=["POST"],
        pattern=r"^/api/v1/ppt/canvas/slide/\{slide_id\}/edit-html$",
        mcp_type=MCPType.TOOL,
    ),
    RouteMap(
        methods=["PATCH"],
        pattern=r"^/api/v1/ppt/canvas/slide/\{slide_id\}/html$",
        mcp_type=MCPType.TOOL,
    ),
]

MCP_CANVAS_TOOL_NAMES = {
    "get_canvas_context_api_v1_ppt_canvas_presentation__presentation_id__context_get": "get_presentation_context",
    "canvas_delete_slide_api_v1_ppt_canvas_slide__slide_id__delete": "delete_slide",
    "canvas_reorder_slide_api_v1_ppt_canvas_slide__slide_id__reorder_patch": "reorder_slide",
    "canvas_get_slide_schema_api_v1_ppt_canvas_schema_get": "get_slide_schema",
    "canvas_edit_slide_api_v1_ppt_canvas_slide__slide_id__edit_post": "edit_slide",
    "canvas_update_slide_api_v1_ppt_canvas_slide__slide_id__patch": "update_slide",
    "canvas_validate_json_api_v1_ppt_canvas_validate_json_post": "validate_json",
    "canvas_create_slide_api_v1_ppt_canvas_slide_create_post": "create_slide",
    "canvas_edit_slide_html_api_v1_ppt_canvas_slide__slide_id__edit_html_post": "edit_slide_html",
    "canvas_update_slide_html_api_v1_ppt_canvas_slide__slide_id__html_patch": "update_slide_html",
}


def get_canvas_route_maps(
    generation_mode: PresentationGenerationMode,
) -> list[RouteMap]:
    route_maps: list[RouteMap] = []
    if generation_mode in {"both", "standard"}:
        route_maps.extend(MCP_CANVAS_STANDARD_ROUTE_MAPS)
    if generation_mode in {"both", "smart"}:
        route_maps.extend(MCP_CANVAS_SMART_ROUTE_MAPS)
    route_maps.extend(MCP_CANVAS_SHARED_ROUTE_MAPS)
    return route_maps


def get_canvas_instructions(generation_mode: PresentationGenerationMode) -> str:
    steps = [
        "Call `get_presentation_context` first to fetch the generation mode, "
        "available layouts, and current slides."
    ]
    if generation_mode in {"both", "standard"}:
        steps.append(
            "Standard decks:\n"
            "   - LLM-powered: call `edit_slide` with a prompt describing the change.\n"
            "   - JSON-direct: call `get_slide_schema` for a layout ID, build JSON that "
            "matches it, check it with `validate_json`, then apply it with "
            "`create_slide` or `update_slide`."
        )
    if generation_mode in {"both", "smart"}:
        steps.append(
            "Smart decks:\n"
            "   - LLM-powered: call `edit_slide_html` with a prompt.\n"
            "   - Direct HTML: call `update_slide_html` to save HTML without an LLM."
        )
    steps.append(
        "Use `delete_slide` and `reorder_slide` to manage the deck. Deleting the "
        "last slide replaces it with a blank slide."
    )
    steps.append(
        "Always share the returned `edit_path` with the user so they can view the "
        "updated deck."
    )
    numbered = "\n".join(f"{i}. {step}" for i, step in enumerate(steps, start=1))
    return f"""
# Canvas Editing Workflow

Edit and extend existing presentations with the canvas tools:

{numbered}
"""
