# Fork modifications

This fork lets coding agents (e.g. Claude Code connected over HTTP MCP to a
headless Presenton server) edit decks with **the same tools Presenton's in-app
assistant uses**, instead of only relaying prompts to the internal LLM.

It bridges upstream's chat tool registry (`services/chat/tools.py` →
`ChatTools`) to HTTP routes, and from there to MCP tools. Tools that upstream
adds or changes are picked up automatically. Mod code lives in new files; edits
to upstream files are a few hook lines (the image build is a separate
`Dockerfile.fork`).

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
| `Dockerfile.fork` | The fork's image build (see below). Upstream's `Dockerfile` is left untouched. |
| `MODS.md` | This file. |

## Fork CI (new files, see PR #2)

| File | Purpose |
| --- | --- |
| `.github/workflows/upstream-sync.yml` | Daily merge of upstream `main` into a sync PR. It runs the FastAPI tests and regenerates `openai_spec.json`. Conflicts and test failures become an `upstream-sync` issue. The PR lists upstream workflow changes and changes to the files below. |
| `.github/workflows/fork-docker.yml` | After `Test All Applications` passes on `main`: native amd64 and arm64 (`ubuntu-24.04-arm`) builds, each smoke-tested and pushed by digest, then one manifest tagged `latest`, `<version>` and `sha-<commit>`. amd64 gates publishing; a failed arm64 publishes the tags amd64-only and warns on the `upstream-sync` issue. |
| `.github/scripts/docker-smoke-test.sh` | Smoke test through nginx: MCP handshake and canvas tools, canvas API, web UI, PDF/PPTX export, offline OCR, offline mem0 embedding. |
| `docker-compose.fork.yml` | Runs the published image on a server: no repo checkout, `.env` passthrough, healthcheck. |

## `Dockerfile.fork`

A copy of upstream's `Dockerfile`, optimised to minimise disk writes on a server that pulls updates often; every difference is marked `Fork:`. Upstream's `Dockerfile` stays untouched, so syncs never conflict on it. Instead, the upstream-sync PR shows upstream's Dockerfile diff: **port relevant changes into `Dockerfile.fork`** (new runtime files or packages, version bumps). The CI smoke test catches a port that breaks the app.

- **Browser:** `chrome-headless-shell` instead of full Chromium, GTK/X11 and ~760 MB of fonts.
  - amd64: Chrome for Testing, at the version the export runtime's puppeteer pins, `en-US` locale only.
  - arm64: Debian's `chromium-headless-shell` at `CHROMIUM_VERSION`.
  - Only the libraries the browser links are installed, plus a `libgbm1` repackaged without its mesa/LLVM dependency.
  - `PUPPETEER_EXECUTABLE_PATH=/usr/local/bin/chrome-headless-shell`; `/usr/bin/chromium` links to it, for upstream's compose file.
  - The build fails if the browser can't resolve a library or print its version.
- **Fonts:** of `fonts-noto-core`, only the Latin/Greek/Cyrillic Sans, Serif and Mono faces, Symbols, Symbols2 and Math (dpkg `path-include`), plus `fonts-noto-color-emoji`. See the known limitations below.
- **Layers:**
  - The backend project isn't installed into `/opt/venv`; a `.pth` file puts `/app/servers/fastapi` on `sys.path`.
  - Every runtime `COPY` uses `--link`.
  - Large, rarely changing parts get their own layers: backend `static/` + `assets/`, Next.js `node_modules`.
  - Backend code is copied last.
- **Fast-moving packages:** `ARG FAST_MOVING_GROUPS` gives each group its own small layer under `/opt/venv-fast/<group>`:
  - `llmai` (6 MB)
  - `docs`: lxml, python-pptx, xlsxwriter, fonttools (36 MB)
  - `mcp`: fastmcp, mcp, httpx2, starlette, … (16 MB)

  The builder sets `PYTHONDONTWRITEBYTECODE=1`, so a reinstall produces a byte-identical venv. 27 of upstream's last 37 lock updates would leave the 552 MB venv layer untouched.
- **Bytecode:** compiled at build time, so containers don't write it:
  - venv: deterministic `unchecked-hash`, before the code copy;
  - backend: `checked-hash`;
  - stdlib: in a stable layer.

  A build-time render bakes the browser's fontconfig caches. A fresh container writes ~0.1 MB.
- **Slimming:**
  - Native libraries are stripped and bundled `tests/` dirs removed.
  - sympy and mpmath are removed (`ARG REMOVE_PACKAGES`).
  - Duplicated or unused Node assets are removed: standalone `public/`, musl and wasm sharp builds, source maps, type definitions, LiteParse's `src/`, tesseract.js's browser builds. LiteParse shares Next.js's libvips when the versions match.
  - Node is installed without headers or npm/corepack (`start.js` only runs npm with `--dev`).
- **OCR:** LiteParse uses tesseract.js (WASM), so only `tesseract-ocr-eng`'s language data is installed. `LITEPARSE_TESSDATA_PATH=/usr/share/tessdata` points at it, so OCR works offline; upstream's image fetches models from a CDN. With `INSTALL_TESSERACT=false`, set `LITEPARSE_TESSDATA_PATH=` (empty) to use the CDN.
- **mem0 model:** `FASTEMBED_CACHE_PATH=/root/.cache/fastembed`, with `BAAI/bge-small-en-v1.5` baked in as its own layer. Upstream downloaded it into the builder's `/tmp` only, so every container fetched it again.

Measured locally:

| | upstream `Dockerfile` | `Dockerfile.fork` |
| --- | --- | --- |
| Image layers | 3,697 MB | 1,857 MB (incl. the 67 MB mem0 model) |
| Re-shipped by a backend-only update | 1,665 MB | 8.2 MB |
| Re-shipped by a frontend-only update | — | 26 MB |
| Re-shipped by an llmai-only lock update | ~1,665 MB | ~14 MB |
| Written by a fresh container | ~66 MB, plus ~67 MB on first mem0 use | ~0.1 MB |

## Upstream files touched

| File | Change | Why | On conflict |
| --- | --- | --- | --- |
| `servers/fastapi/api/v1/ppt/router.py` | +1 import, +1 `include_router(CANVAS_ROUTER)` at the end | Hook for the mod. | Take upstream, re-add both lines. |
| `servers/fastapi/mcp_server.py` | +1 import from `mcp_canvas`; `MCP_TOOL_NAMES.update(MCP_CANVAS_TOOL_NAMES)` after the dict; `route_maps.extend(get_canvas_route_maps(generation_mode))` before the final `EXCLUDE` map; `instructions += get_canvas_instructions(generation_mode)` before `return instructions` | Hook for the mod. | Take upstream, re-add the four lines. The route-map line must stay before the catch-all `RouteMap(mcp_type=MCPType.EXCLUDE)`. |
| `servers/fastapi/tests/unit/test_mcp_server_auth.py` | +1 import; `test_mcp_tools_follow_presentation_generation_mode` asserts `expected_tools \| get_canvas_tool_names(generation_mode)` | Hook for the mod. | Take upstream, re-add the import and the `\| get_canvas_tool_names(...)` on that assert. |
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

## Known limitations

- **Latin-only fallback fonts (by choice).** The image keeps only Latin/Greek/Cyrillic, symbol, math and emoji fonts. Slides normally carry their own web fonts, but Arabic, Hebrew, Indic, CJK or Thai text in a font without those glyphs renders as boxes in exports. To support them, widen the `path-include` lines in `Dockerfile.fork` (or add `fonts-noto-cjk`).
- **Unpinned downloads.** These two downloads are not hash-pinned:
  - **Chrome for Testing** comes over HTTPS from Google's Chrome for Testing bucket, at the exact version the export runtime's puppeteer lockfile pins. Google publishes no per-archive checksums, and hardcoding one would break following puppeteer's version.
  - **The mem0 model** (`BAAI/bge-small-en-v1.5`) is fetched by name from Hugging Face, because fastembed doesn't accept a revision. The step only re-runs when `uv.lock` changes, and the snapshot directory in `/root/.cache/fastembed` records the revision in use.
- **Smart-slide HTML can make the export browser fetch URLs (inherited).** HTML saved with `smartSaveSlide`, or by the in-app chat, may reference `http(s)` URLs, which headless Chrome fetches from inside the container during preview/export (SSRF). Upstream's sanitizer strips scripts and event handlers, not URLs. MCP adds API-key callers to the in-app chat's existing exposure. That's acceptable for a single-user, self-hosted install with trusted keys; restrict subresource URLs before exposing MCP to untrusted callers.
- **Concurrent slide-index changes aren't locked.** This matches upstream's chat paths and is fine for single-user use.

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
