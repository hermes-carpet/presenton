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
| `Dockerfile` | **Layers:** the backend project is not installed into `/opt/venv` (a constant `presenton-backend.pth` puts `/app/servers/fastapi` on `sys.path`); the spaCy model install sits above the code copy; every runtime `COPY` uses `--link`; large, rarely changing parts get their own layers (backend `static/` + `assets/`, Next.js `node_modules`) and backend code is copied last. **Fast-moving packages:** groups that change far more often than the rest of `uv.lock` (from upstream's 37 lock updates) live in their own small layers under `/opt/venv-fast/<group>` (each added via a `.pth` file; `ARG FAST_MOVING_GROUPS`, and one runtime `COPY` per group): `llmai` (Presenton's LLM client, 14 changes, mostly alone; 6 MB), `docs` (lxml, python-pptx, xlsxwriter, fonttools, which bump together; 36 MB), `mcp` (fastmcp, mcp, httpx2, starlette, … from its last bump; 16 MB). With the builder's `PYTHONDONTWRITEBYTECODE=1` a reinstall produces a byte-identical venv, so 27 of those 37 updates would leave the 552 MB venv layer untouched (verified by swapping llmai 0.3.15 → 0.3.14: ~14 MB re-shipped instead of ~669 MB). sympy and mpmath are removed (`ARG REMOVE_PACKAGES`; only onnxruntime's offline model tools import them). **Bytecode (minimises runtime disk writes):** the venv is compiled in its own step before the code copy (deterministic `unchecked-hash` pycs, so the layer changes only with `uv.lock`), the backend after the code copy (`checked-hash`), and the stdlib (which the official Python image ships without bytecode) in a stable runtime layer; the headless browser renders once at build time so its bundled fontconfig caches are baked in, and huggingface_hub's timestamped logs are removed so the cache dir copy is deterministic. A fresh container writes ~0.1 MB instead of ~66 MB. **Slimming:** venv native libraries stripped, packages' `tests/` removed, the standalone build's duplicate `public/` and musl sharp builds removed, source maps/type definitions/sharp wasm removed from the export and LiteParse node trees, LiteParse's duplicate `src/` and tests and tesseract.js's inlined browser builds (`*.wasm.js`) removed, LiteParse's libvips linked to Next.js's copy when the sharp/libvips versions match, Node stripped and installed without headers or npm/corepack (dpkg `path-exclude`; npm only runs with `start.js --dev`). **OCR:** LiteParse uses tesseract.js (WASM), so only `tesseract-ocr-eng` (language data) is installed, without the tesseract binary, its libraries or `libicu76`; `LITEPARSE_TESSDATA_PATH=/usr/share/tessdata` points at it, so OCR also works offline (upstream's image downloads models from a CDN). With `INSTALL_TESSERACT=false`, set `LITEPARSE_TESSDATA_PATH=` (empty) to fall back to the CDN. **mem0 model:** `FASTEMBED_CACHE_PATH=/root/.cache/fastembed`, with mem0's default embedding model (`BAAI/bge-small-en-v1.5`) baked in before the code copy and copied as its own layer; upstream's warm-up downloaded it to `/tmp/fastembed_cache` in the builder only, so every container re-downloaded it (and failed offline). **Browser:** full Chromium + GTK/X11 + ~760 MB of fonts are replaced by `chrome-headless-shell` (amd64: Chrome for Testing at the version the export runtime's puppeteer pins, installed in `assets-builder`, `en-US` locale only; arm64: Debian's `chromium-headless-shell` at `CHROMIUM_VERSION`), only the libraries it links, a `libgbm1` repackaged without its mesa/LLVM dependency, and `fonts-noto-core` (only Latin/Greek/Cyrillic Sans, Serif, Mono, Symbols, Symbols2 and Math, via dpkg `path-include`; 54 → 18 MB with emoji) + `fonts-noto-color-emoji`. The build-time render that bakes the browser's fontconfig caches is best-effort, because Chromium's GPU process crashes under QEMU (cross-arch builds); exports themselves work there. `PUPPETEER_EXECUTABLE_PATH=/usr/local/bin/chrome-headless-shell`; `/usr/bin/chromium` links to it for upstream's compose file. The build fails if the browser is missing a library. | Measured locally: image layers 3,697 MB → 1,857 MB (incl. the 67 MB mem0 model); a backend-only update re-ships 8.2 MB (was 1,665 MB), a frontend-only update 26 MB, an llmai-only lock update ~14 MB; a fresh container writes ~0.1 MB at runtime (was ~66 MB of bytecode, plus a ~67 MB mem0 model download on first use). PDF/PPTX export, template previews and MCP generation keep working (CI smoke test exports both formats and runs offline OCR). | Take upstream, then re-apply. If upstream changed the runtime package list, keep their non-GUI additions; if they bumped puppeteer, nothing to do (the headless shell follows it). |
| `nginx.conf` | `proxy_redirect` in the `/mcp/` location rewrites FastMCP's slash redirect (`http://localhost:8001/mcp`, built from the internal Host) to the public origin (`X-Forwarded-*`, else the request's Host) | Remote MCP clients that use `/mcp/` can follow the redirect instead of being sent to localhost. | Take upstream, re-add the `proxy_redirect` line in `location /mcp/`. |
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
