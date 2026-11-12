import json
import re
from typing import Any
from urllib.parse import unquote, urlsplit

import aiohttp
from openai import APIError as OpenAIAPIError
from openai import AsyncOpenAI, DefaultAsyncHttpxClient
from google import genai
from google.genai.errors import APIError as GoogleAPIError
from yarl import URL

from utils.provider_error_messages import safe_provider_error_detail


class ModelAvailabilityError(Exception):
    def __init__(self, provider: str, message: str, *, provider_status_code: Any):
        try:
            status_code = int(provider_status_code)
        except (TypeError, ValueError):
            status_code = 500

        self.provider = provider
        self.raw_message = message
        self.provider_status_code = status_code
        self.status_code = 400 if 400 <= status_code < 500 else 500
        super().__init__(
            safe_provider_error_detail(
                provider,
                "model validation",
                status_code=status_code,
                message=message,
            )
        )


def _payload_error_message(payload: Any) -> str | None:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("detail") or error.get("code")
            if message:
                return str(message)
        if isinstance(error, str) and error:
            return error

        message = payload.get("message") or payload.get("detail")
        if message:
            return str(message)
        return None

    if isinstance(payload, str) and payload:
        return payload

    return None


async def _aiohttp_error_message(response: aiohttp.ClientResponse) -> str:
    text = await response.text()
    if text:
        try:
            message = _payload_error_message(json.loads(text))
            if message:
                return message
        except json.JSONDecodeError:
            return text

    return response.reason or f"Provider returned HTTP {response.status}"


async def _raise_for_model_response(
    response: aiohttp.ClientResponse, *, provider: str
) -> None:
    if response.status < 400:
        return

    raise ModelAvailabilityError(
        provider,
        await _aiohttp_error_message(response),
        provider_status_code=response.status,
    )


def _openai_error_message(error: OpenAIAPIError) -> str:
    message = _payload_error_message(getattr(error, "body", None))
    if message:
        return message

    return getattr(error, "message", None) or str(error)


def _google_error_message(error: GoogleAPIError) -> str:
    return getattr(error, "message", None) or str(error)


def normalize_openai_compatible_base_url(url: str) -> str:
    """Ensure base URL targets the OpenAI-compatible /v1 root (LiteLLM, vLLM, etc.)."""
    u = (url or "").strip().rstrip("/")
    if not u:
        return u
    if u.endswith("/v1"):
        return u
    base = u.split("?", 1)[0]
    if "/v1" in base:
        return u
    return f"{u}/v1"


def is_together_api_base_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return parsed.scheme in {"http", "https"} and (
            (parsed.hostname or "").lower().rstrip(".")
            in {"api.together.ai", "api.together.xyz"}
        )
    except ValueError:
        return False


def _validated_model_discovery_base_url(url: str) -> str:
    try:
        if any(ord(char) < 32 or ord(char) == 127 for char in url) or "\\" in url:
            raise ValueError("Invalid URL characters")
        value = url.strip()
        parsed = urlsplit(value)
        decoded_path = unquote(parsed.path)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.query or parsed.fragment
            or any(char in decoded_path for char in "\\%?#")
            or any(ord(char) < 32 or ord(char) == 127 for char in decoded_path)
            or any(part in {".", ".."} for part in decoded_path.split("/"))
        ):
            raise ValueError("Invalid API base URL")
        _ = parsed.port
        base_url = normalize_openai_compatible_base_url(str(URL(value)))
    except (ValueError, UnicodeError) as error:
        raise ModelAvailabilityError(
            "OpenAI-compatible provider", "Invalid model discovery URL", provider_status_code=400
        ) from error
    if not re.fullmatch(
        r"https?://(?:[A-Za-z0-9._~!$&'()*+,;=:%-]+@)?"
        r"(?:[A-Za-z0-9._~-]+|\[[0-9A-Fa-f:.]+(?:%[A-Za-z0-9._~-]+)?\])"
        r"(?::[0-9]+)?(?:/[A-Za-z0-9._~!$&'()*+,;=:@%/-]*)?",
        base_url,
    ):
        raise ModelAvailabilityError(
            "OpenAI-compatible provider", "Invalid model discovery URL", provider_status_code=400
        )
    return base_url


def _model_ids_from_openai_compatible_payload(data: object) -> list[str]:
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("data", [])
        if not isinstance(items, list):
            return []
    else:
        return []

    model_ids: list[str] = []
    for item in items:
        if isinstance(item, str) and item:
            model_ids.append(item)
            continue
        if not isinstance(item, dict):
            continue
        model_id = item.get("id") or item.get("name")
        if isinstance(model_id, str) and model_id:
            model_ids.append(model_id)
    return model_ids


async def list_together_models(url: str, api_key: str) -> list[str]:
    """
    Together's /v1/models payload is not always parsed by the OpenAI Python SDK
    (which expects a paginated object and calls _set_private_attributes on it).
    """
    base_url = _validated_model_discovery_base_url(url)
    models_url = f"{base_url.rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {(api_key or '').strip()}"}

    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(models_url, allow_redirects=False) as response:
            if 300 <= response.status < 400:
                raise ModelAvailabilityError(
                    "Together", "Model discovery redirects are not allowed; configure the final API URL",
                    provider_status_code=502,
                )
            await _raise_for_model_response(response, provider="Together")
            data = await response.json()

    return _model_ids_from_openai_compatible_payload(data)


async def list_available_openai_compatible_models(url: str, api_key: str) -> list[str]:
    url = _validated_model_discovery_base_url(url)
    if is_together_api_base_url(url):
        return await list_together_models(url, api_key)

    # Local LiteLLM / OpenAI-compatible proxies often omit auth; SDK rejects a blank key.
    effective_key = (api_key or "").strip() or "EMPTY"
    async with AsyncOpenAI(
        api_key=effective_key,
        base_url=url,
        http_client=DefaultAsyncHttpxClient(follow_redirects=False),
    ) as client:
        try:
            response = await client.models.with_raw_response.list()
            # Custom proxies can return Together's list shape on any hostname.
            return _model_ids_from_openai_compatible_payload(response.http_response.json())
        except OpenAIAPIError as e:
            raise ModelAvailabilityError(
                "OpenAI-compatible provider",
                _openai_error_message(e),
                provider_status_code=getattr(e, "status_code", None) or 500,
            ) from e


async def list_available_anthropic_models(api_key: str) -> list[str]:
    async with aiohttp.ClientSession(
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
    ) as session:
        async with session.get(
            "https://api.anthropic.com/v1/models",
            params={"limit": 50},
        ) as response:
            await _raise_for_model_response(response, provider="Anthropic")
            data = await response.json()

    models = data.get("data", [])
    return [model.get("id") for model in models if model.get("id")]


async def list_available_google_models(api_key: str) -> list[str]:
    try:
        client = genai.Client(api_key=api_key)
        return [x.name for x in client.models.list(config={"page_size": 50}) if x.name]
    except GoogleAPIError as e:
        raise ModelAvailabilityError(
            "Google",
            _google_error_message(e),
            provider_status_code=getattr(e, "code", None)
            or getattr(e, "status_code", None)
            or 500,
        ) from e
