#!/usr/bin/env bash
# Start a Presenton image and check that the app and the canvas MCP tools are
# served through nginx, the way a remote MCP client reaches them. The MCP URL is
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
echo "Smoke test passed."
