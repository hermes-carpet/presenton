import asyncio
import json

import httpx
import pytest
from openai import AsyncOpenAI, DefaultAsyncHttpxClient

from api.v1.ppt.endpoints import openai as endpoint
from utils import available_models as models


@pytest.fixture
def transport(monkeypatch):
    aio_calls, sdk_calls, clients = [], [], []

    class Response:
        status = 200
        payload = {"data": [{"id": "control-model"}]}
        reason = "Control response"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def json(self, **_kwargs):
            return self.payload

        async def text(self):
            return json.dumps(self.payload)

    response = Response()

    class Session:
        def __init__(self, **kwargs):
            self.headers = kwargs.get("headers", {})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def get(self, url, **kwargs):
            aio_calls.append((url, kwargs, self.headers))
            return response

    def handle(request):
        sdk_calls.append(request)
        if request.url.host == "redirect-target.invalid":
            return httpx.Response(200, json={"data": [{"id": "escaped"}]})
        headers = {"x-should-retry": "false"}
        if 300 <= response.status < 400:
            headers["Location"] = "http://redirect-target.invalid/private"
        return httpx.Response(response.status, json=response.payload, headers=headers)

    def http_client(**kwargs):
        return DefaultAsyncHttpxClient(transport=httpx.MockTransport(handle), **kwargs)

    def sdk_client(**kwargs):
        if "http_client" not in kwargs:
            kwargs["http_client"] = http_client()
        client = AsyncOpenAI(**kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(models.aiohttp, "ClientSession", Session)
    monkeypatch.setattr(models, "DefaultAsyncHttpxClient", http_client, raising=False)
    monkeypatch.setattr(models, "AsyncOpenAI", sdk_client)
    return aio_calls, sdk_calls, clients, response


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/private#api.together.ai", "http://127.0.0.1/private?x=api.together.ai",
    "https://api.together.ai/v1?x=", "https://api.together.ai/v1#x",
    "http://localhost:1234/v1/../private", "http://localhost:1234/v1/%2e%2e/private",
    "http://localhost:1234/v1/%252e%252e/private", "http://localhost:1234/proxy%2f..%2fprivate",
    "http://localhost:1234/proxy/%5cprivate", "http://localhost:1234/proxy/%0aprivate",
    "http://localhost:1234/proxy/%3fprivate", "http://localhost:1234/proxy/%23private",
    "http://localhost:1234/proxy\\private", "http://local\thost:1234/v1", "http://localhost:1234/\x7f",
    "http://localhost:99999/v1", "http://localhost:invalid/v1", "file:///private", "",
])
@pytest.mark.parametrize("direct", [False, True])
def test_invalid_roots_never_reach_either_transport(transport, url, direct):
    aio_calls, sdk_calls, _, _ = transport
    operation = models.list_together_models if direct else models.list_available_openai_compatible_models
    with pytest.raises(models.ModelAvailabilityError) as error:
        asyncio.run(operation(url, "control-key"))
    assert error.value.status_code == 400
    assert aio_calls == []
    assert sdk_calls == []


@pytest.mark.parametrize("url", [
    "https://api.together.ai.other.example/v1", "https://other.example/api.together.ai/v1",
    "https://api.together.ai@other.example/v1", "https://other.example/v1?target=api.together.xyz",
    "https://other.example/v1#api.together.ai",
])
def test_together_detection_uses_hostname_only(url):
    assert models.is_together_api_base_url(url) is False


@pytest.mark.parametrize("url, expected", [
    ("https://api.together.ai", "https://api.together.ai/v1/models"),
    ("https://api.together.xyz/v1/", "https://api.together.xyz/v1/models"),
    ("https://API.TOGETHER.AI:443/v1", "https://api.together.ai/v1/models"),
    ("https://api.together.ai./v1", "https://api.together.ai./v1/models"),
])
def test_official_together_aliases_preserve_dispatch(transport, url, expected):
    aio_calls, sdk_calls, _, _ = transport
    assert asyncio.run(models.list_available_openai_compatible_models(url, " control-key ")) == ["control-model"]
    assert aio_calls == [(expected, {"allow_redirects": False}, {"Authorization": "Bearer control-key"})]
    assert sdk_calls == []


@pytest.mark.parametrize("payload, expected", [
    ([{"id": "first"}, {"name": "legacy"}], ["first", "legacy"]),
    (["first", {"id": "second"}], ["first", "second"]),
    ({"data": [{"id": "first"}]}, ["first"]),
])
@pytest.mark.parametrize("url", ["https://api.together.ai/v1", "http://proxy.example/api.together.ai/v1"])
def test_together_and_custom_proxy_payloads_remain_supported(transport, payload, expected, url):
    _, _, clients, response = transport
    response.payload = payload
    assert asyncio.run(models.list_available_openai_compatible_models(url, "control-key")) == expected
    assert all(client.is_closed() for client in clients)


@pytest.mark.parametrize("url, expected", [
    ("http://localhost:1234", "http://localhost:1234/v1/models"),
    ("http://host.docker.internal:4000/", "http://host.docker.internal:4000/v1/models"),
    ("http://192.168.0.10:8000/proxy/api/v1/", "http://192.168.0.10:8000/proxy/api/v1/models"),
    ("https://api.fireworks.ai/inference/v1", "https://api.fireworks.ai/inference/v1/models"),
    ("http://[::1]:1234/v1", "http://[::1]:1234/v1/models"),
    ("http://[fe80::1%en0]:1234/v1", "http://[fe80::1%en0]:1234/v1/models"),
    ("https://b\u00fccher.example/\u63a5\u53e3/v1", "https://xn--bcher-kva.example/%E6%8E%A5%E5%8F%A3/v1/models"),
    ("http://proxy.example/my%20proxy/v1", "http://proxy.example/my%20proxy/v1/models"),
    ("http://user:p%40ss@proxy.example/v1", "http://user:p%40ss@proxy.example/v1/models"),
])
def test_sdk_preserves_custom_local_and_proxy_roots(transport, url, expected):
    aio_calls, sdk_calls, clients, _ = transport
    assert asyncio.run(models.list_available_openai_compatible_models(url, "")) == ["control-model"]
    assert aio_calls == []
    assert [str(call.url) for call in sdk_calls] == [expected]
    if "@" not in expected:
        assert sdk_calls[0].headers["Authorization"] == "Bearer EMPTY"
    assert len(clients) == 1 and clients[0].is_closed()


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("url", ["https://api.together.ai/v1", "http://localhost:1234/v1"])
def test_both_transports_reject_redirects_without_followup(transport, status, url):
    aio_calls, sdk_calls, clients, response = transport
    response.status = status
    with pytest.raises(models.ModelAvailabilityError):
        asyncio.run(models.list_available_openai_compatible_models(url, "control-key"))
    assert len(aio_calls) + len(sdk_calls) == 1
    assert all(call.url.host != "redirect-target.invalid" for call in sdk_calls)
    assert all(call[1]["allow_redirects"] is False for call in aio_calls)
    assert all(client.is_closed() for client in clients)


@pytest.mark.parametrize("status, expected", [(401, 400), (503, 500)])
@pytest.mark.parametrize("url", ["https://api.together.ai/v1", "http://localhost:1234/v1"])
def test_provider_error_mapping_and_sdk_cleanup_are_preserved(transport, status, expected, url):
    _, _, clients, response = transport
    response.status = status
    response.payload = {"error": {"message": "Control upstream error"}}
    with pytest.raises(models.ModelAvailabilityError) as error:
        asyncio.run(models.list_available_openai_compatible_models(url, "control-key"))
    assert error.value.status_code == expected
    assert all(client.is_closed() for client in clients)


def test_endpoint_reports_invalid_discovery_url_as_client_error(transport):
    from fastapi import HTTPException

    aio_calls, sdk_calls, _, _ = transport
    with pytest.raises(HTTPException) as error:
        asyncio.run(endpoint.get_available_models("http://localhost/private#api.together.ai", "control-key"))
    assert error.value.status_code == 400
    assert aio_calls == [] and sdk_calls == []
