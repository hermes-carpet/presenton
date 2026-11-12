from typing import Annotated, Any
import re
from urllib.parse import quote, unquote, urlsplit

import aiohttp
from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field
from utils.get_env import get_openrouter_base_url_env


OPENROUTER_ROUTER = APIRouter(prefix="/openrouter", tags=["OpenRouter"])
DEFAULT_OPENROUTER_API_URL = "https://openrouter.ai/api/v1"
_MODEL_DISCOVERY_PATH = re.compile(
    r"models/([A-Za-z0-9_~:@-][A-Za-z0-9._~:@-]*)/([A-Za-z0-9_~:@-][A-Za-z0-9._~:@-]*)/endpoints"
)


def _url_parts(value: str) -> tuple[tuple[str, str, int], str]:
    try:
        parsed = urlsplit(value.strip())
        path = unquote(parsed.path).rstrip("/")
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query or parsed.fragment
            or "\\" in value or "\\" in path
            or any(ord(char) < 32 or ord(char) == 127 for char in value + path)
            or any(part in {".", ".."} for part in path.split("/"))
        ):
            raise ValueError("Invalid discovery URL")
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
        host = parsed.hostname.encode("idna").decode("ascii").lower()
        return (parsed.scheme, host, port), path
    except (ValueError, UnicodeError) as error:
        raise HTTPException(status_code=400, detail="Invalid OpenRouter discovery URL") from error


def _approved_api_roots():
    yield DEFAULT_OPENROUTER_API_URL
    configured = (get_openrouter_base_url_env() or "").strip().rstrip("/")
    if configured and configured != DEFAULT_OPENROUTER_API_URL:
        yield configured


def _select_api_root(requested: str | None) -> str:
    target = _url_parts((requested or DEFAULT_OPENROUTER_API_URL).strip().rstrip("/"))
    for approved in _approved_api_roots():
        if target == _url_parts(approved):
            # Return server-owned configuration, never the request-supplied URL.
            return approved
    raise HTTPException(status_code=400, detail="OpenRouter base URL must match the default or server-configured OPENROUTER_BASE_URL")


def _validated_discovery_url(url: str) -> str:
    origin, path = _url_parts(url)
    for approved in _approved_api_roots():
        approved_origin, prefix = _url_parts(approved)
        if origin != approved_origin:
            continue
        if path == f"{prefix}/providers":
            return f"{approved}/providers"
        if path.startswith(f"{prefix}/"):
            model_path = path[len(prefix) + 1:]
            if _MODEL_DISCOVERY_PATH.fullmatch(model_path):
                return f"{approved}/{quote(model_path, safe='/')}"
    raise HTTPException(status_code=400, detail="Invalid OpenRouter discovery endpoint")


class OpenRouterProviderOption(BaseModel):
    value: str
    label: str
    available: bool = True
    context_length: int | None = None
    max_completion_tokens: int | None = None
    supported_parameters: list[str] = Field(default_factory=list)


def _provider_option(item: dict[str, Any]) -> OpenRouterProviderOption | None:
    value = item.get("tag") or item.get("slug") or item.get("provider_name")
    label = item.get("provider_name") or item.get("name") or value
    if not isinstance(value, str) or not value.strip():
        return None
    status = item.get("status")
    available = status not in {"down", "unavailable", "disabled"}
    parameters = item.get("supported_parameters")
    return OpenRouterProviderOption(
        value=value.strip(),
        label=str(label or value).strip(),
        available=available,
        context_length=item.get("context_length")
        if isinstance(item.get("context_length"), int)
        else None,
        max_completion_tokens=item.get("max_completion_tokens")
        if isinstance(item.get("max_completion_tokens"), int)
        else None,
        supported_parameters=[str(value) for value in parameters]
        if isinstance(parameters, list)
        else [],
    )


async def _get_json(
    session: aiohttp.ClientSession, url: str
) -> tuple[int, Any]:
    destination = _validated_discovery_url(url)
    async with session.get(destination, allow_redirects=False) as response:
        if 300 <= response.status < 400:
            raise HTTPException(status_code=502, detail="OpenRouter discovery redirects are not allowed; configure the final API URL")
        try:
            payload = await response.json(content_type=None)
        except Exception:
            payload = None
        return response.status, payload


@OPENROUTER_ROUTER.post(
    "/providers/available", response_model=list[OpenRouterProviderOption]
)
async def get_available_openrouter_providers(
    model: Annotated[str, Body()],
    api_key: Annotated[str, Body()],
    base_url: Annotated[str | None, Body()] = None,
):
    model = model.strip()
    api_key = api_key.strip()
    if not model:
        raise HTTPException(status_code=400, detail="OpenRouter model is required")
    if not api_key:
        raise HTTPException(status_code=400, detail="OpenRouter API key is required")

    api_root = _select_api_root(base_url)
    headers = {"Authorization": f"Bearer {api_key}"}
    async with aiohttp.ClientSession(headers=headers) as session:
        endpoint_items: list[dict[str, Any]] = []
        if "/" in model:
            author, slug = model.split("/", 1)
            endpoint_url = (
                f"{api_root}/models/{quote(author, safe='')}/"
                f"{quote(slug, safe='')}/endpoints"
            )
            status, payload = await _get_json(session, endpoint_url)
            if status < 400 and isinstance(payload, dict):
                data = payload.get("data", payload)
                if isinstance(data, dict) and isinstance(data.get("endpoints"), list):
                    endpoint_items = [
                        item for item in data["endpoints"] if isinstance(item, dict)
                    ]

        if not endpoint_items:
            status, payload = await _get_json(session, f"{api_root}/providers")
            if status >= 400:
                raise HTTPException(
                    status_code=400 if status < 500 else 502,
                    detail="OpenRouter could not list providers for this model",
                )
            data = payload.get("data", []) if isinstance(payload, dict) else payload
            if isinstance(data, list):
                endpoint_items = [item for item in data if isinstance(item, dict)]

    options = [option for item in endpoint_items if (option := _provider_option(item))]
    deduplicated = {option.value: option for option in options}
    return sorted(deduplicated.values(), key=lambda option: option.label.lower())
