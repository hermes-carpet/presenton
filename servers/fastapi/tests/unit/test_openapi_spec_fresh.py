import json
from pathlib import Path

from api.main import app

SPEC_PATH = Path(__file__).resolve().parents[2] / "openai_spec.json"
REGENERATE = "cd servers/fastapi && PYTHONPATH=. uv run --locked python scripts/generate_openapi_spec.py"


def test_static_openapi_spec_matches_app():
    """The MCP server builds its tools from the static spec; keep it in sync."""
    committed = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    generated = json.loads(json.dumps(app.openapi()))

    assert committed == generated, (
        f"servers/fastapi/openai_spec.json is stale. Regenerate it with: {REGENERATE}"
    )
