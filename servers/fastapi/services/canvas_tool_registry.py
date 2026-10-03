"""Presenton's in-app assistant tools, as exposed over HTTP/MCP by the canvas mod.

The tools come from services.chat.tools.ChatTools, the registry the in-app
assistant itself uses, so tools added or changed upstream are picked up
without changes here. See MODS.md.
"""

import copy
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel

from services.chat.tools import ChatTools

DeckType = Literal["standard", "smart"]
DECK_TYPES: tuple[DeckType, ...] = ("standard", "smart")


@dataclass(frozen=True)
class CanvasTool:
    # Name the in-app assistant (and its system prompt) uses.
    tool_name: str
    # MCP tool name; differs from tool_name only when Standard and Smart decks
    # define the same tool with different arguments.
    mcp_name: str
    deck_types: frozenset[DeckType]
    description: str
    input_schema: dict[str, Any]

    @property
    def path(self) -> str:
        return f"/presentation/{{presentation_id}}/tools/{self.mcp_name}"

    @property
    def operation_id(self) -> str:
        return f"canvas_tool_{self.mcp_name}"


class _DeckTypeOnly:
    """ChatTools reads only presentation_type when listing definitions."""

    def __init__(self, presentation_type: DeckType):
        self.presentation_type = presentation_type


def _chat_tool_definitions(deck_type: DeckType) -> dict[str, Any]:
    tools = ChatTools(_DeckTypeOnly(deck_type))  # type: ignore[arg-type]
    return {definition.name: definition for definition in tools.get_tool_definitions()}


def inline_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Return the model's JSON schema with local $defs references inlined."""
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})

    def resolve(value: Any, stack: tuple[str, ...]) -> Any:
        if isinstance(value, dict):
            ref = value.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                name = ref.removeprefix("#/$defs/")
                if name in stack:
                    raise ValueError(f"Recursive schema in {model.__name__}: {name}")
                resolved = resolve(copy.deepcopy(definitions[name]), stack + (name,))
                siblings = {key: item for key, item in value.items() if key != "$ref"}
                return {**resolved, **resolve(siblings, stack)}
            return {key: resolve(item, stack) for key, item in value.items()}
        if isinstance(value, list):
            return [resolve(item, stack) for item in value]
        return value

    return resolve(schema, ())


def _merge_descriptions(standard: str, smart: str) -> str:
    if standard == smart:
        return standard
    return f"Standard decks: {standard}\n\nSmart decks: {smart}"


def _smart_variant_name(name: str) -> str:
    return "smart" + name[:1].upper() + name[1:]


@lru_cache(maxsize=1)
def get_canvas_tools() -> tuple[CanvasTool, ...]:
    standard = _chat_tool_definitions("standard")
    smart = _chat_tool_definitions("smart")
    tools: list[CanvasTool] = []

    for name, definition in standard.items():
        smart_definition = smart.get(name)
        shared = (
            smart_definition is not None
            and smart_definition.input_schema is definition.input_schema
        )
        tools.append(
            CanvasTool(
                tool_name=name,
                mcp_name=name,
                deck_types=frozenset(DECK_TYPES if shared else ("standard",)),
                description=(
                    _merge_descriptions(definition.description, smart_definition.description)
                    if shared
                    else definition.description
                ),
                input_schema=inline_json_schema(definition.input_schema),
            )
        )

    for name, definition in smart.items():
        if name in standard and standard[name].input_schema is definition.input_schema:
            continue
        tools.append(
            CanvasTool(
                tool_name=name,
                mcp_name=_smart_variant_name(name) if name in standard else name,
                deck_types=frozenset(("smart",)),
                description=definition.description,
                input_schema=inline_json_schema(definition.input_schema),
            )
        )

    return tuple(tools)


def get_canvas_tools_for_deck(deck_type: DeckType) -> tuple[CanvasTool, ...]:
    return tuple(tool for tool in get_canvas_tools() if deck_type in tool.deck_types)
