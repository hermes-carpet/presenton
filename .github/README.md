# Presenton (hermes-carpet fork)

> **This is a modified fork of [presenton/presenton](https://github.com/presenton/presenton).**
> It tracks upstream `main` daily. Upstream's own README is at [`README.md`](../README.md);
> every change is listed in [`MODS.md`](../MODS.md).

The fork is meant for running Presenton as a headless web server in Docker,
with coding agents (Claude Code, Codex, …) connected to it over HTTP MCP.

## What's different from upstream

- **Canvas tools over MCP.** MCP clients get the same deck-editing tools as
  Presenton's built-in assistant (`getSlideAtIndex`, `addElement`, `saveSlide`,
  …), plus `get_presentation_context` (deck type, slides, the assistant's own
  editing guide) and `reorder_slide`. Upstream's MCP server only exposes
  generation endpoints. Tools are taken from upstream's chat tool registry,
  so tools that upstream adds show up automatically.
- **Supervised MCP server.** If the MCP process crashes it is logged and
  restarted, instead of `/mcp` returning 502 until the container restarts.
- **Remote-friendly MCP.** `/mcp/` redirects go to the public origin rather than
  to an internal `localhost:8001` address. Links returned to MCP clients use
  `PRESENTON_PUBLIC_URL`.
- **Smaller, update-friendly image** (`Dockerfile.fork`):

  | | upstream | fork |
  | --- | --- | --- |
  | Image layers | 3,697 MB | 1,857 MB |
  | Re-downloaded after a backend-only update | 1,665 MB | 8 MB |
  | Written by a fresh container | ~66 MB (+67 MB on first mem0 use) | ~0.1 MB |

  It ships `chrome-headless-shell` instead of full Chromium. PDF and PPTX
  export still work. Fonts are trimmed to Latin, Greek and Cyrillic, plus
  symbols and emoji. OCR and the mem0 embedding model work offline.
- **Images for amd64 and arm64**: `ghcr.io/hermes-carpet/presenton`. Each
  build is native and smoke-tested.

## Run it

```bash
curl -O https://raw.githubusercontent.com/hermes-carpet/presenton/main/docker-compose.fork.yml
docker compose -f docker-compose.fork.yml up -d
```

Provider settings go in an optional `.env` next to the file. To pin a version,
set `PRESENTON_IMAGE_TAG` (e.g. `0.9.11`, no `v`). Set `PRESENTON_PUBLIC_URL`
to the address you open Presenton at, so that links in MCP results work.

Then connect an agent to `http://<server>:5001/mcp`, with a Presenton API key
as a bearer token. For example:

```bash
claude mcp add --transport http presenton http://<server>:5001/mcp \
  --header "Authorization: Bearer sk-presenton-..."
```
