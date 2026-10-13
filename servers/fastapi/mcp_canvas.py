"""Canvas editing tools for the MCP server.

Exposes Presenton's in-app assistant tools (see services/canvas_tool_registry.py)
plus deck context and slide reordering. Kept out of mcp_server.py so upstream
changes there merge cleanly; mcp_server.py only calls the hooks below. See MODS.md.

The tools are read from openai_spec.json, never from the tool registry: importing
services.chat loads the ML stack (~150 MB) and services.temp_file_service, which
empties TEMP_DIRECTORY on import, under FastAPI's feet.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from fastmcp.server.providers.openapi import MCPType, RouteMap

from utils.get_env import PresentationGenerationMode

OPENAPI_SPEC_PATH = Path(__file__).with_name("openai_spec.json")
CANVAS_PREFIX = "/api/v1/ppt/canvas"
TOOL_OPERATION_PREFIX = "canvas_tool_"
# Same values as DECK_TYPES and DECK_TYPES_EXTENSION in canvas_tool_registry.
DECK_TYPES = ("standard", "smart")
DECK_TYPES_EXTENSION = "x-presenton-deck-types"

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


@dataclass(frozen=True)
class _SpecTool:
    operation_id: str
    mcp_name: str
    path: str
    deck_types: frozenset[str]


HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def _spec_error(message: str) -> RuntimeError:
    return RuntimeError(f"openai_spec.json: {message}; regenerate the spec (see MODS.md).")


def _load_spec_tools() -> tuple[_SpecTool, ...]:
    with OPENAPI_SPEC_PATH.open("r", encoding="utf-8") as f:
        spec = json.load(f)
    tools: dict[str, _SpecTool] = {}
    for path, path_item in spec["paths"].items():
        for method, operation in path_item.items():
            if method not in HTTP_METHODS:
                continue
            operation_id = operation.get("operationId", "")
            if not operation_id.startswith(TOOL_OPERATION_PREFIX):
                continue
            mcp_name = operation_id.removeprefix(TOOL_OPERATION_PREFIX)
            expected_path = f"{CANVAS_PREFIX}/presentation/{{presentation_id}}/tools/{mcp_name}"
            if method != "post" or path != expected_path:
                raise _spec_error(f"{operation_id} is {method.upper()} {path}, expected POST {expected_path}")
            if mcp_name in tools:
                raise _spec_error(f"duplicate canvas operation {operation_id}")
            deck_types = operation.get(DECK_TYPES_EXTENSION)
            if (
                not isinstance(deck_types, list)
                or not deck_types
                or not set(deck_types) <= set(DECK_TYPES)
            ):
                raise _spec_error(f"{operation_id} has no valid {DECK_TYPES_EXTENSION}")
            tools[mcp_name] = _SpecTool(
                operation_id=operation_id,
                mcp_name=mcp_name,
                path=path,
                deck_types=frozenset(deck_types),
            )
    if not tools:
        raise _spec_error("no canvas tool routes")
    return tuple(tools.values())


_SPEC_TOOLS = _load_spec_tools()

MCP_CANVAS_TOOL_NAMES = {
    "get_canvas_context_api_v1_ppt_canvas_presentation__presentation_id__context_get": "get_presentation_context",
    "canvas_reorder_slide_api_v1_ppt_canvas_slide__slide_id__reorder_patch": "reorder_slide",
    **{tool.operation_id: tool.mcp_name for tool in _SPEC_TOOLS},
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
            pattern=f"^{re.escape(tool.path)}$",
            mcp_type=MCPType.TOOL,
        )
        for tool in _SPEC_TOOLS
        if tool.deck_types & deck_types
    )
    return route_maps


def get_canvas_tool_names(generation_mode: PresentationGenerationMode) -> set[str]:
    deck_types = _deck_types_for_mode(generation_mode)
    return {"get_presentation_context", "reorder_slide"} | {
        tool.mcp_name for tool in _SPEC_TOOLS if tool.deck_types & deck_types
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
