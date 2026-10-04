#!/usr/bin/env bash
# Start a Presenton image and check, through nginx (the way a remote MCP client
# reaches it): the MCP handshake and canvas tools, the canvas API, the web UI,
# PDF/PPTX export with the bundled headless browser, offline OCR, and the
# offline mem0 embedding model. The MCP URL is
# /mcp without a trailing slash (/mcp/ redirects to an internal address).
# Usage: docker-smoke-test.sh <image> [host-port]
set -euo pipefail

image=$1
port=${2:-5001}
name="presenton-smoke-$$"
base="http://127.0.0.1:${port}"

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    echo "--- container logs ---"
    docker logs --tail 200 "$name" 2>&1 || true
  fi
  docker rm -f "$name" >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT

docker run -d --name "$name" -p "${port}:80" \
  -e DISABLE_AUTH=true \
  -e MIGRATE_DATABASE_ON_STARTUP=true \
  -e PRESENTON_PUBLIC_URL="$base" \
  "$image" >/dev/null

mcp() {
  # $1: JSON-RPC body; prints response headers+body
  curl -sS -i -X POST "${base}/mcp" \
    -H "content-type: application/json" \
    -H "accept: application/json, text/event-stream" \
    ${session:+-H "mcp-session-id: ${session}"} \
    -d "$1"
}

init='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"1"}}}'
session=""
echo "Waiting for the MCP endpoint..."
for _ in $(seq 1 90); do
  if response=$(mcp "$init" 2>/dev/null) && grep -qi '^mcp-session-id:' <<<"$response"; then
    break
  fi
  sleep 5
done
session=$(grep -i '^mcp-session-id:' <<<"$response" | head -1 | cut -d: -f2- | tr -d ' \r')
if [ -z "$session" ]; then
  echo "MCP endpoint did not come up"
  exit 1
fi
echo "MCP session: ${session}"

mcp '{"jsonrpc":"2.0","method":"notifications/initialized"}' >/dev/null
tools=$(mcp '{"jsonrpc":"2.0","id":2,"method":"tools/list"}')
for tool in get_presentation_context reorder_slide getSlideAtIndex addElement saveSlide smartSaveSlide; do
  if ! grep -q "\"name\":\"${tool}\"" <<<"$tools"; then
    echo "MCP tool missing: ${tool}"
    exit 1
  fi
done
echo "MCP lists the canvas tools."

# Expect a status code from a GET, retrying while the backend is still starting
# (nginx answers 502 until FastAPI finishes migrations).
expect_status() {
  local expected=$1 path=$2 code=""
  for _ in $(seq 1 60); do
    code=$(curl -sS -o /dev/null -w '%{http_code}' "${base}${path}" || true)
    [ "$code" = "$expected" ] && return 0
    [ "$code" = "502" ] || [ "$code" = "000" ] || break
    sleep 5
  done
  echo "Expected ${expected} from ${path}, got ${code}"
  return 1
}

missing="00000000-0000-0000-0000-000000000000"
expect_status 404 "/api/v1/ppt/canvas/presentation/${missing}/context"
expect_status 200 "/"
# Export: seed a Smart deck, save a slide through the canvas tool, then export.
deck=$(docker exec -i -w / "$name" python - <<'PY'
import asyncio
from models.sql.presentation import PresentationModel, PresentationVersion
from models.sql.slide import SlideModel
from services.database import async_session_maker

async def main():
    deck = PresentationModel(version=PresentationVersion.V2_STANDARD, content="smoke",
                             n_slides=1, language="English",
                             title="Smoke", layout=None, generation_mode="smart")
    slide = SlideModel(presentation=deck.id, layout_group="smart-html", layout="smart-html",
                       index=0, content={}, html_content="<section></section>")
    async with async_session_maker() as session:
        session.add_all([deck, slide])
        await session.commit()
    print(deck.id)

asyncio.run(main())
PY
)
deck=$(tail -n 1 <<<"$deck")
html='<section data-slide-type=\"content\" data-slide-title=\"Smoke\" class=\"relative h-[720px] w-[1280px] overflow-hidden bg-white p-16\"><h2 class=\"text-5xl\">Smoke test ✓</h2></section>'
code=$(curl -sS -o /dev/null -w '%{http_code}' -X POST \
  "${base}/api/v1/ppt/canvas/presentation/${deck}/tools/smartSaveSlide" \
  -H "content-type: application/json" \
  -d "{\"html\":\"${html}\",\"index\":0,\"replaceOldSlideAtIndex\":true,\"speakerNote\":null,\"editPrompt\":null}")
if [ "$code" != "200" ]; then
  echo "smartSaveSlide returned ${code}"
  exit 1
fi
for format in pdf pptx; do
  response=$(curl -sS -X POST "${base}/api/v1/ppt/presentation/${deck}/export" \
    -H "content-type: application/json" -d "{\"export_as\":\"${format}\"}")
  path=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("path", ""))' <<<"$response" 2>/dev/null || true)
  if [ -z "$path" ] || ! docker exec "$name" test -s "$path"; then
    echo "${format} export failed: ${response}"
    exit 1
  fi
  echo "${format} export OK."
done

# OCR: document extraction must work without network access, using the
# bundled language data (no CDN download).
ocr=$(docker run --rm --network none --entrypoint sh "$image" -c '
  font=$(find /usr/share/fonts -name "NotoSans-Regular.ttf" | head -1)
  magick -size 900x200 xc:white -font "$font" -pointsize 56 -fill black \
    -annotate +30+120 "Quarterly OCR test 2026" /tmp/ocr.png
  cd /app/servers/fastapi && python -c "
from services.liteparse_service import LiteParseService
print(LiteParseService().parse_to_markdown(\"/tmp/ocr.png\"))"' 2>&1 | tail -n 5 || true)
if ! grep -q "Quarterly OCR test 2026" <<<"$ocr"; then
  echo "Offline OCR failed: ${ocr}"
  exit 1
fi
echo "Offline OCR OK."

# mem0: its default embedding model must be baked into the image.
embedding=$(docker run --rm --network none --entrypoint sh "$image" -c '
  cd /app/servers/fastapi && python -c "
from mem0.configs.embeddings.base import BaseEmbedderConfig
from mem0.embeddings.fastembed import FastEmbedEmbedding
model = FastEmbedEmbedding(BaseEmbedderConfig(model=\"BAAI/bge-small-en-v1.5\", embedding_dims=384))
print(\"dims\", len(model.embed(\"quarterly results\")))"' 2>&1 | tail -n 5 || true)
if ! grep -q "dims 384" <<<"$embedding"; then
  echo "Offline mem0 embedding failed: ${embedding}"
  exit 1
fi
echo "Offline mem0 embedding OK."

echo "Smoke test passed."
