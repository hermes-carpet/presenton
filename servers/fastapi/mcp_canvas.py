"""Canvas editing tools for the MCP server.

Exposes Presenton's in-app assistant tools (see services/canvas_tool_registry.py)
plus deck context and slide reordering. Kept out of mcp_server.py so upstream
changes there merge cleanly; mcp_server.py only calls the hooks below. See MODS.md.
"""

import re

from fastmcp.server.providers.openapi import MCPType, RouteMap

from services.canvas_tool_registry import DECK_TYPES, get_canvas_tools
from utils.get_env import PresentationGenerationMode

CANVAS_PREFIX = "/api/v1/ppt/canvas"

MCP_CANVAS_SHARED_ROUTE_MAPS = [
    RouteMap(
        methods=["GET"],
        pattern=rf"^{CANVAS_PREFIX}/presentation/\{{presentation_id\}}/context$",
        mcp_type=MCPType.TOOL,
    ),
    RouteMap(
        methods=["PATCH"],
        pattern=rf"^{CANVAS_PREFIX}/slide/\{{slide_id\}}/reorder$",
        mcp_type=MCPType.TOOL,
    ),
]

MCP_CANVAS_TOOL_NAMES = {
    "get_canvas_context_api_v1_ppt_canvas_presentation__presentation_id__context_get": "get_presentation_context",
    "canvas_reorder_slide_api_v1_ppt_canvas_slide__slide_id__reorder_patch": "reorder_slide",
    **{tool.operation_id: tool.mcp_name for tool in get_canvas_tools()},
}


def _deck_types_for_mode(generation_mode: PresentationGenerationMode) -> set[str]:
    # Unknown modes fall back to "both", like get_presentation_generation_mode.
    return {generation_mode} if generation_mode in DECK_TYPES else set(DECK_TYPES)


def get_canvas_route_maps(
    generation_mode: PresentationGenerationMode,
) -> list[RouteMap]:
    deck_types = _deck_types_for_mode(generation_mode)
    route_maps = list(MCP_CANVAS_SHARED_ROUTE_MAPS)
    route_maps.extend(
        RouteMap(
            methods=["POST"],
            pattern=f"^{re.escape(CANVAS_PREFIX + tool.path)}$",
            mcp_type=MCPType.TOOL,
        )
        for tool in get_canvas_tools()
        if tool.deck_types & deck_types
    )
    return route_maps


def get_canvas_tool_names(generation_mode: PresentationGenerationMode) -> set[str]:
    deck_types = _deck_types_for_mode(generation_mode)
    return {"get_presentation_context", "reorder_slide"} | {
        tool.mcp_name for tool in get_canvas_tools() if tool.deck_types & deck_types
    }


def get_canvas_instructions(generation_mode: PresentationGenerationMode) -> str:
    return """
# Editing existing presentations

The camelCase tools (getSlideAtIndex, saveSlide, addElement, ...) are the same
tools Presenton's built-in assistant uses to edit a deck. Use them directly
instead of asking another model to make the edit.

1. Call `get_presentation_context` first. It returns the deck type, the slides,
   the tools that apply to this deck, and `editing_guide`: the protocol the
   built-in assistant follows. Follow it (slide indexes are 0-based).
2. Every editing tool takes `presentation_id`. A tool that does not apply to
   the deck's type returns an error naming the right call.
3. Failed tool calls return the assistant's error and recovery guidance; fix
   the arguments and retry.
4. Use `reorder_slide` to move a slide. Share the returned `edit_path` with the
   user so they can open the deck.
"""
