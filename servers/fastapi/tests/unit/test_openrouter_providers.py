import asyncio
from urllib.parse import quote
import pytest
from fastapi import HTTPException

from api.v1.ppt.endpoints import openrouter


class TransportResponse:
    def __init__(self, status=200, payload=None):
        self.status = status
        self.payload = payload if payload is not None else {"data": [{"slug": "control", "name": "Control"}]}

    async def __aenter__(self): return self
    async def __aexit__(self, *_args): return None
    async def json(self, **_kwargs): return self.payload


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
    calls, responses, sessions = [], {}, []

    class Session:
        def __init__(self, **kwargs): sessions.append(kwargs)
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return None
        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return responses.get(url, TransportResponse())

    monkeypatch.setattr(openrouter.aiohttp, "ClientSession", Session)
    return calls, responses, sessions


@pytest.mark.parametrize("base_url", [
    "http://127.0.0.1:9999/private", "http://169.254.169.254/latest/meta-data", "http://[::1]:9999/private",
    "https://other.example/api/v1", "https://openrouter.ai.other.example/api/v1", "https://openrouter.ai:444/api/v1",
    "https://openrouter.ai@127.0.0.1/api/v1", "https://openrouter.ai/api/v1?query=1", "https://openrouter.ai/api/v1#fragment",
    "//openrouter.ai/api/v1", "file:///api/v1", "https://openrouter.ai/api/v1/%2e%2e/%2e%2e/private",
    "https://openrouter.ai/api/v1/%252e%252e/private", "https://openrouter.ai/api/v1\\private",
])
def test_unconfigured_roots_never_reach_provider_transport(transport, base_url):
    calls, _, _ = transport
    with pytest.raises(HTTPException) as error:
        asyncio.run(openrouter.get_available_openrouter_providers(model="alias", api_key="control-key", base_url=base_url))
    assert error.value.status_code == 400
    assert calls == []


@pytest.mark.parametrize("model", [
    "../x", "openai/..", "openai/../x", "%2e%2e/x", "openai/%252e%252e", "openai/model?x=1",
    "openai/model#fragment", "openai\\other/model", "openai//model", "openai/model%2fname",
])
def test_model_path_representations_cannot_select_other_endpoints(transport, model):
    calls, _, _ = transport
    with pytest.raises(HTTPException) as error:
        asyncio.run(openrouter.get_available_openrouter_providers(model=model, api_key="control-key", base_url=None))
    assert error.value.status_code == 400
    assert calls == []


@pytest.mark.parametrize("base_url", [None, "https://openrouter.ai/api/v1/", "https://OPENROUTER.AI:443/api/v1"])
def test_official_alias_discovery_selects_approved_transport_value(transport, base_url):
    calls, _, sessions = transport
    result = asyncio.run(openrouter.get_available_openrouter_providers(model="alias", api_key=" control-key ", base_url=base_url))
    assert [provider.value for provider in result] == ["control"]
    assert calls == [("https://openrouter.ai/api/v1/providers", {"allow_redirects": False})]
    assert sessions[0]["headers"] == {"Authorization": "Bearer control-key"}


def test_configured_private_proxy_and_model_variants_remain_supported(monkeypatch, transport):
    root = "http://127.0.0.1:9999/proxy/openrouter/api/v1"
    monkeypatch.setenv("OPENROUTER_BASE_URL", root)
    calls, responses, _ = transport
    endpoint = root + "/models/openai/gpt-4o%3Afree/endpoints"
    responses[endpoint] = TransportResponse(payload={"data": {"endpoints": [{"tag": "groq", "provider_name": "Groq"}]}})
    result = asyncio.run(openrouter.get_available_openrouter_providers(model="openai/gpt-4o:free", api_key="control-key", base_url=root + "/"))
    assert [provider.value for provider in result] == ["groq"]
    assert calls == [(endpoint, {"allow_redirects": False})]


@pytest.mark.parametrize("model", ["~deepseek/deepseek-pro-latest", "@preset/draft", "vendor/model_name:free"])
def test_routed_model_aliases_remain_supported(transport, model):
    calls, responses, _ = transport
    author, slug = model.split("/", 1)
    endpoint = f"https://openrouter.ai/api/v1/models/{quote(author, safe='')}/{quote(slug, safe='')}/endpoints"
    responses[endpoint] = TransportResponse(payload={"data": {"endpoints": [{"tag": "control", "provider_name": "Control"}]}})
    result = asyncio.run(openrouter.get_available_openrouter_providers(model=model, api_key="control-key", base_url=None))
    assert [provider.value for provider in result] == ["control"]
    assert calls == [(endpoint, {"allow_redirects": False})]


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_provider_redirects_do_not_trigger_followup_requests(transport, status):
    calls, responses, _ = transport
    responses["https://openrouter.ai/api/v1/providers"] = TransportResponse(status, {})
    with pytest.raises(HTTPException) as error:
        asyncio.run(openrouter.get_available_openrouter_providers(model="alias", api_key="control-key", base_url=None))
    assert error.value.status_code == 502
    assert calls == [("https://openrouter.ai/api/v1/providers", {"allow_redirects": False})]


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/providers", "https://openrouter.ai/api/v1-other/providers", "https://openrouter.ai/api/v1/models/../x/endpoints",
    "https://openrouter.ai/api/v1/models/openai/%252e%252e/endpoints", "https://openrouter.ai/api/v1/providers?url=private",
])
def test_shared_transport_checks_complete_discovery_url(transport, url):
    calls, _, _ = transport
    with pytest.raises(HTTPException):
        asyncio.run(openrouter._get_json(openrouter.aiohttp.ClientSession(), url))
    assert calls == []


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return None


def test_openrouter_provider_discovery_normalizes_model_endpoints(monkeypatch):
    monkeypatch.setattr(
        openrouter.aiohttp,
        "ClientSession",
        lambda **_kwargs: FakeSession(),
    )

    async def fake_get_json(_session, url):
        assert url.endswith("/models/openai/gpt-4o/endpoints")
        return 200, {
            "data": {
                "endpoints": [
                    {
                        "tag": "groq",
                        "provider_name": "Groq",
                        "status": "available",
                        "context_length": 131072,
                        "max_completion_tokens": 32768,
                        "supported_parameters": ["tools", "reasoning"],
                    }
                ]
            }
        }

    monkeypatch.setattr(openrouter, "_get_json", fake_get_json)

    result = asyncio.run(
        openrouter.get_available_openrouter_providers(
            model="openai/gpt-4o",
            api_key="key",
            base_url=None,
        )
    )

    assert result[0].value == "groq"
    assert result[0].label == "Groq"
    assert result[0].max_completion_tokens == 32768


def test_openrouter_provider_discovery_falls_back_to_global_list(monkeypatch):
    monkeypatch.setattr(
        openrouter.aiohttp,
        "ClientSession",
        lambda **_kwargs: FakeSession(),
    )
    responses = iter(
        [
            (404, {}),
            (200, {"data": [{"slug": "together", "name": "Together"}]}),
        ]
    )

    async def fake_get_json(_session, _url):
        return next(responses)

    monkeypatch.setattr(openrouter, "_get_json", fake_get_json)

    result = asyncio.run(
        openrouter.get_available_openrouter_providers(
            model="custom/alias",
            api_key="key",
            base_url="https://openrouter.ai/api/v1",
        )
    )

    assert [provider.value for provider in result] == ["together"]
