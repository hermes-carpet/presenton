# Fork modifications

This fork adds canvas slide editing for MCP clients on top of upstream
[presenton/presenton](https://github.com/presenton/presenton). Mod code lives in
new files. Edits to upstream files are kept to small hook lines so upstream
merges stay easy.

Use this list when resolving merge conflicts with upstream.

## New files (mod only; upstream never touches these)

| File | Purpose |
| --- | --- |
| `servers/fastapi/api/v1/ppt/endpoints/canvas.py` | `/api/v1/ppt/canvas/*` endpoints: context, slide schema, validate, create, update, LLM edit, HTML edit/update, delete, reorder. |
| `servers/fastapi/mcp_canvas.py` | MCP route maps, operationId → tool-name mapping, and the per-mode canvas section of the MCP instructions. |
| `servers/fastapi/tests/unit/test_canvas_api.py` | Endpoint tests against a real in-memory SQLite session. |
| `servers/fastapi/tests/unit/test_mcp_canvas.py` | Checks that the canvas tools are registered, exist in the OpenAPI spec, and are documented per mode. |
| `MODS.md` | This file. |

## Upstream files touched

| File | Change | Why | On conflict |
| --- | --- | --- | --- |
| `servers/fastapi/api/v1/ppt/router.py` | +1 import, +1 `include_router(CANVAS_ROUTER)` at the end | Mounts the canvas endpoints. | Take upstream, then re-add both lines. |
| `servers/fastapi/mcp_server.py` | +1 import from `mcp_canvas`; `MCP_TOOL_NAMES.update(MCP_CANVAS_TOOL_NAMES)` after the dict; `route_maps.extend(get_canvas_route_maps(generation_mode))` before the final `EXCLUDE` map in `get_mcp_route_maps`; `instructions += get_canvas_instructions(generation_mode)` before `return instructions` in `get_mcp_instructions` | Exposes the canvas endpoints as MCP tools, only in the generation modes they support. | Take upstream, then re-add the four lines. The route-map line must stay before the catch-all `RouteMap(mcp_type=MCPType.EXCLUDE)`. |
| `servers/fastapi/openai_spec.json` | Regenerated; now includes the `/api/v1/ppt/canvas/*` paths | The MCP server builds its tools from this static spec. | Never hand-merge (it's one line). Take either side, then regenerate (below). |
| `servers/fastapi/tests/unit/test_mcp_server_auth.py` | Added the ten canvas tool names to the expected tool sets in `test_mcp_tools_follow_presentation_generation_mode` | The test lists every exposed tool per mode. | Take upstream, then re-add the canvas names to each mode's set. |

### Regenerating `openai_spec.json`

From `servers/fastapi`, with the same env vars as the test job:

```bash
PYTHONPATH=. uv run --locked python scripts/generate_openapi_spec.py
```

`scripts/generate_openapi_spec.py` is upstream's script; it dumps `api.main.app.openapi()`.
Upstream's committed spec can lag behind its own code, so a regenerated spec
may also include unrelated upstream schema changes. That's expected.

## Upstream internals the mod depends on

`canvas.py` reuses these instead of copying them, so canvas edits match the
generation path. If upstream renames or changes any of them, `test_canvas_api.py`
fails:

- `api.v1.ppt.endpoints.presentation._get_presentation_stream_layout`: schema, validate, create, update, edit, and context tests
- `api.v1.ppt.endpoints.presentation._template_slide_ui` / `_apply_template_content_to_ui`: create, update, and edit tests assert the fetched image reaches `slide.ui`
- `api.v1.ppt.endpoints.presentation._presentation_response_data`: context tests
- `services.chat.memory_layer.PresentationChatMemoryLayer.delete_slide`: delete tests (index shift, `n_slides`, blank fallback slide for Standard and Smart decks)
- `utils.process_slides.process_slide_and_fetch_assets` / `process_old_and_new_slides_and_fetch_assets` (public, but signature-coupled)
