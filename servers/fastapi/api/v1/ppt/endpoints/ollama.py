from typing import List
import os
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from constants.supported_ollama_models import get_supported_ollama_models
from models.ollama_model_metadata import OllamaModelMetadata
from models.ollama_model_status import OllamaModelStatus
from utils.ollama import list_available_ollama_models, pull_ollama_model, get_ollama_library_models
from utils.get_env import is_disable_auth_enabled

OLLAMA_ROUTER = APIRouter(prefix="/ollama", tags=["Ollama"])


def _http_origin(value: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            return None
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
        return parsed.scheme, parsed.hostname.lower(), port
    except ValueError:
        return None


def _require_provider_request_intent(request: Request) -> None:
    trusted = {_http_origin(str(request.url))}
    for name in ("PRESENTON_PUBLIC_URL", "NEXT_PUBLIC_URL", "NEXT_PUBLIC_FAST_API"):
        trusted.add(_http_origin(os.getenv(name, "")))
    for origin in os.getenv("PRESENTON_DESKTOP_ORIGINS", "").split(","):
        trusted.add(_http_origin(origin.strip()))
    trusted.discard(None)
    reported = [request.headers[name] for name in ("origin", "referer") if name in request.headers]
    if reported:
        if all(_http_origin(value) in trusted for value in reported):
            return
    elif request.headers.get("sec-fetch-site") == "same-origin":
        return
    raise HTTPException(status_code=403, detail="Custom provider discovery requires a trusted Origin or Referer")


@OLLAMA_ROUTER.get("/models/supported", response_model=List[OllamaModelMetadata])
async def get_supported_models():
    try:
        pulled_models = await list_available_ollama_models()
    except Exception:
        pulled_models = []

    return get_supported_ollama_models(pulled_models)
@OLLAMA_ROUTER.get("/models/available", response_model=List[OllamaModelStatus])
async def get_available_models(request: Request, ollama_url: str | None = None):
    # SameSite=Lax cookies accompany cross-site top-level GET navigations.
    # An override must originate from the app, not merely carry an admin cookie.
    if ollama_url is not None and not is_disable_auth_enabled():
        _require_provider_request_intent(request)
    return await list_available_ollama_models(ollama_url)


@OLLAMA_ROUTER.get("/models/library")
async def get_library_models():
    return get_ollama_library_models()

@OLLAMA_ROUTER.post("/models/pull")
async def pull_model(model_name: str, ollama_url: str | None = None):
    await list_available_ollama_models(ollama_url)
    return StreamingResponse(
        pull_ollama_model(model_name, ollama_url),
        media_type="text/event-stream",
    )
