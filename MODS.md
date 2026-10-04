# Fork modifications

This fork lets coding agents (e.g. Claude Code connected over HTTP MCP to a
headless Presenton server) edit decks with **the same tools Presenton's in-app
assistant uses**, instead of only relaying prompts to the internal LLM.

It bridges upstream's chat tool registry (`services/chat/tools.py` →
`ChatTools`) to HTTP routes, and from there to MCP tools. Tools that upstream
adds or changes are picked up automatically. Mod code lives in new files; edits
to upstream files are a few hook lines.

Use this file as the checklist when resolving merge conflicts with upstream.

## How it works

- `services/canvas_tool_registry.py` lists `ChatTools.get_tool_definitions()` for
  Standard and Smart decks. A tool both deck types share gets one route. If the
  two deck types define the same name with different arguments, the Smart
  variant is named `smart<Name>` (currently only `smartSaveSlide`).
- `api/v1/ppt/endpoints/canvas.py` registers
  `POST /api/v1/ppt/canvas/presentation/{presentation_id}/tools/<tool>` per tool. It
  runs `ChatTools.execute_tool_call()` exactly as the chat service does, including
  the assistant's argument repair. Failures return 422 with the assistant's
  error and recovery guidance. It also serves:
  - `GET .../presentation/{id}/context` (`get_presentation_context`): deck type,
    slides, the tool-name map, and `editing_guide` (the in-app assistant's
    system prompt for that deck type).
  - `PATCH .../slide/{id}/reorder` (`reorder_slide`); the chat tools have no reorder.
- `mcp_canvas.py` exposes those routes as MCP tools for the enabled generation
  mode(s), and adds a short section to the MCP instructions.

## New files (mod only; upstream never touches these)

| File | Purpose |
| --- | --- |
| `servers/fastapi/services/canvas_tool_registry.py` | Derives the exposed tools from `ChatTools`. |
| `servers/fastapi/api/v1/ppt/endpoints/canvas.py` | Tool bridge routes, deck context, reorder. |
| `servers/fastapi/mcp_canvas.py` | MCP route maps, tool names, instructions. |
| `servers/fastapi/tests/unit/test_canvas_api.py` | Bridge, context and reorder tests against a real in-memory SQLite DB, plus an end-to-end MCP test. |
| `servers/fastapi/tests/unit/test_mcp_canvas.py` | Registration, spec and per-mode exposure tests. |
| `servers/fastapi/tests/unit/test_openapi_spec_fresh.py` | Fails CI when `openai_spec.json` doesn't match `app.openapi()`. |
| `MODS.md` | This file. |

## Upstream files touched

| File | Change | Why | On conflict |
| --- | --- | --- | --- |
| `servers/fastapi/api/v1/ppt/router.py` | +1 import, +1 `include_router(CANVAS_ROUTER)` at the end | Hook for the mod. | Take upstream, re-add both lines. |
| `servers/fastapi/mcp_server.py` | +1 import from `mcp_canvas`; `MCP_TOOL_NAMES.update(MCP_CANVAS_TOOL_NAMES)` after the dict; `route_maps.extend(get_canvas_route_maps(generation_mode))` before the final `EXCLUDE` map; `instructions += get_canvas_instructions(generation_mode)` before `return instructions` | Hook for the mod. | Take upstream, re-add the four lines. The route-map line must stay before the catch-all `RouteMap(mcp_type=MCPType.EXCLUDE)`. |
| `servers/fastapi/tests/unit/test_mcp_server_auth.py` | +1 import; `test_mcp_tools_follow_presentation_generation_mode` asserts `expected_tools \| get_canvas_tool_names(generation_mode)` | Hook for the mod. | Take upstream, re-add the import and the `\| get_canvas_tool_names(...)` on that assert. |
| `Dockerfile` | `fastapi-builder`: the backend project is no longer installed into `/opt/venv`; a constant `presenton-backend.pth` puts `/app/servers/fastapi` on `sys.path` instead (same import order). The spaCy model install moved above the code copy. Runtime: every `COPY` uses `--link`, and the backend code is copied last. | With upstream's Dockerfile, a code-only backend change rewrote 1,665 MB of layers (incl. the 930 MB venv). Now 65 MB (measured locally), so builds and server pulls after a mod or sync update are much smaller. | Take upstream, then re-apply these edits (they don't change runtime behavior). |
| `servers/fastapi/openai_spec.json` | Regenerated; includes the canvas routes and every tool's input schema | MCP tools are built from this static file. | Never hand-merge (one line). The upstream-sync workflow resolves a spec-only conflict and regenerates automatically. By hand: take either side, then regenerate (below). |

### Regenerating `openai_spec.json`

From `servers/fastapi`, with the same env vars as the test job:

```bash
PYTHONPATH=. uv run --locked python scripts/generate_openapi_spec.py
```

`scripts/generate_openapi_spec.py` is upstream's script; it dumps
`api.main.app.openapi()`. The output is deterministic and doesn't depend on env
vars. Upstream changing a chat tool's schema also changes the spec, and
`tests/unit/test_openapi_spec_fresh.py` fails until it's regenerated.

## Upstream APIs the mod depends on

If upstream renames or reshapes any of these, `test_canvas_api.py` fails:

- `services.chat.tools.ChatTools`: constructor `(memory)`,
  `get_tool_definitions()` (reads only `memory.presentation_type`),
  `execute_tool_call(AssistantToolCall)` and its `{"ok", "result" | "error", "repair", "recovery"}` result
- `services.chat.presentation_context_store.PresentationContextStore(sql_session, presentation_id, presentation_type=...)`
- `services.chat.prompts.build_system_prompt(presentation_memory_context, chat_memory_context, presentation_type)`
- `llmai.shared.AssistantToolCall(id, name, arguments)`
- Tool input schemas must not be recursive (`canvas_tool_registry.inline_json_schema`
  inlines `$defs`; recursion raises).
