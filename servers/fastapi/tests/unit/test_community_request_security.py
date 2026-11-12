import asyncio
import json

import pytest
from fastapi import HTTPException

from services import community_presentations as community


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setenv("PRESENTON_COMMUNITY_ENABLED", "true")
    monkeypatch.delenv("PRESENTON_COMMUNITY_API_URL", raising=False)
    calls, sessions = [], []

    class Response:
        status = 200
        payload = {"id": 7, "slides": ["<section>Control</section>"]}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def read(self):
            return json.dumps(self.payload).encode()

    response = Response()

    class Session:
        def __init__(self, **kwargs):
            sessions.append(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return response

    monkeypatch.setattr(community.aiohttp, "ClientSession", Session)
    return calls, sessions, response


@pytest.mark.parametrize("path", [
    "/../admin", "/%2e%2e/admin", "/%252e%252e/admin", "//other.example/private",
    "https://other.example/private", "/7/../admin", "/7?redirect=private", "/7#fragment",
    "/7\\admin", "/7/", "/-1", "/0", "/01", "/1.0", "/\u0661", "/7\n",
])
def test_shared_transport_rejects_non_catalog_paths_before_network(transport, path):
    calls, sessions, _ = transport
    with pytest.raises(HTTPException) as error:
        asyncio.run(community._cloud_get(path))
    assert error.value.status_code == 422
    assert calls == []
    assert sessions == []


@pytest.mark.parametrize("root", [
    community.DEFAULT_COMMUNITY_API_URL,
    "http://127.0.0.1:8080/custom/community/",
    "https://community.example/proxy/presentations",
])
def test_configured_catalog_and_positive_id_preserve_query_parameters(monkeypatch, transport, root):
    monkeypatch.setenv("PRESENTON_COMMUNITY_API_URL", root)
    calls, sessions, response = transport
    filters = {"page": 2, "created_at_gt": "2026-01-01?next=http://127.0.0.1/#x"}
    assert asyncio.run(community._cloud_get("", filters)) == response.payload
    assert asyncio.run(community.get_community_presentation(7)) == response.payload
    assert calls == [
        (root.rstrip("/"), {"params": filters, "allow_redirects": False}),
        (root.rstrip("/") + "/7", {"params": None, "allow_redirects": False}),
    ]
    assert all(session["trust_env"] is True for session in sessions)


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("path", ["", "/7"])
def test_catalog_and_presentation_redirects_are_errors(transport, status, path):
    calls, _, response = transport
    response.status = status
    with pytest.raises(HTTPException) as error:
        asyncio.run(community._cloud_get(path))
    assert error.value.status_code == 502
    assert error.value.detail["code"] == "community_redirect_not_allowed"
    assert error.value.detail["retryable"] is False
    assert len(calls) == 1
    assert calls[0][1]["allow_redirects"] is False


@pytest.mark.parametrize("status, expected", [(404, 404), (422, 422), (429, 429), (503, 503)])
def test_existing_upstream_error_mapping_is_preserved(transport, status, expected):
    _, _, response = transport
    response.status = status
    response.payload = {"detail": "Control upstream error"}
    with pytest.raises(HTTPException) as error:
        asyncio.run(community.get_community_presentation(7))
    assert error.value.status_code == expected
    assert isinstance(error.value.detail["retryable"], bool)


def test_disabled_community_opens_no_transport(monkeypatch, transport):
    calls, sessions, _ = transport
    monkeypatch.setenv("PRESENTON_COMMUNITY_ENABLED", "false")
    with pytest.raises(HTTPException) as error:
        asyncio.run(community._cloud_get("/7"))
    assert error.value.status_code == 404
    assert calls == []
    assert sessions == []
