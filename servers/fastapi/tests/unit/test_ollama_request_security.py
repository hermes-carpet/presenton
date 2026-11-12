import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import middlewares
from api.v1.auth.principal import AuthPrincipal
from api.v1.ppt.endpoints import ollama


@pytest.fixture
def model_client(monkeypatch):
    class Session:
        async def scalar(self, _query): return 1

    class Context:
        async def __aenter__(self): return Session()
        async def __aexit__(self, *_args): return None

    async def principal(*_args): return AuthPrincipal(uuid.uuid4(), "admin", True, "jwt"), object()
    async def no_proxy(*_args, **_kwargs): return None

    for name in ("PRESENTON_PUBLIC_URL", "NEXT_PUBLIC_URL", "NEXT_PUBLIC_FAST_API", "PRESENTON_DESKTOP_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(middlewares, "is_disable_auth_enabled", lambda: False)
    monkeypatch.setattr(ollama, "is_disable_auth_enabled", lambda: False)
    monkeypatch.setattr(middlewares, "async_session_maker", Context)
    monkeypatch.setattr(middlewares, "resolve_request_principal", principal)
    monkeypatch.setattr(middlewares, "maybe_proxy_presenton_cloud_request", no_proxy)
    models = AsyncMock(return_value=[])
    monkeypatch.setattr(ollama, "list_available_ollama_models", models)
    app = FastAPI()
    app.add_middleware(middlewares.SessionAuthMiddleware)
    app.include_router(ollama.OLLAMA_ROUTER, prefix="/api/v1/ppt")
    with TestClient(app) as client:
        yield client, models


@pytest.mark.parametrize("headers", [
    {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document", "Referer": "https://untrusted.example/page"},
    {"Referer": "https://untrusted.example/page"},
    {},
    {"Origin": "null"},
    {"Origin": "http://testserver.untrusted.example"},
    {"Origin": "http://testserver@untrusted.example"},
    {"Origin": "http://testserver:81"},
    {"Origin": "http://testserver", "Referer": "https://untrusted.example/page"},
])
@pytest.mark.parametrize("target", ["http://127.0.0.1/admin/action?item=1&x=", "http://[::1]/admin/action?x="])
def test_custom_provider_navigation_never_reaches_network_helper(model_client, headers, target):
    client, models = model_client
    response = client.get("/api/v1/ppt/ollama/models/available", params={"ollama_url": target}, headers=headers)
    assert response.status_code == 403
    models.assert_not_awaited()


@pytest.mark.parametrize("headers", [
    {"Origin": "http://testserver"},
    {"Origin": "http://TESTSERVER:80"},
    {"Referer": "http://testserver/settings"},
    {"Sec-Fetch-Site": "same-origin"},
])
def test_same_origin_custom_local_provider_requests_preserve_override(model_client, headers):
    client, models = model_client
    target = "http://127.0.0.1:11434/custom"
    assert client.get("/api/v1/ppt/ollama/models/available", params={"ollama_url": target}, headers=headers).status_code == 200
    models.assert_awaited_once_with(target)


def test_configured_split_frontend_origin_can_test_unsaved_provider(monkeypatch, model_client):
    client, models = model_client
    monkeypatch.setenv("NEXT_PUBLIC_URL", "https://slides.example/app/")
    target = "http://192.168.0.10:11434"
    response = client.get("/api/v1/ppt/ollama/models/available", params={"ollama_url": target}, headers={"Origin": "https://slides.example", "Referer": "https://slides.example/app/settings", "Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 200
    models.assert_awaited_once_with(target)


def test_default_discovery_preserves_headerless_api_requests(model_client):
    client, models = model_client
    assert client.get("/api/v1/ppt/ollama/models/available").status_code == 200
    models.assert_awaited_once_with(None)


@pytest.mark.parametrize("desktop", [False, True])
def test_auth_disabled_api_preserves_headerless_custom_providers(monkeypatch, model_client, desktop):
    client, models = model_client
    monkeypatch.setattr(middlewares, "is_disable_auth_enabled", lambda: True)
    monkeypatch.setattr(ollama, "is_disable_auth_enabled", lambda: True)
    headers = {}
    if desktop:
        monkeypatch.setenv("PRESENTON_ELECTRON", "true")
        monkeypatch.setenv("PRESENTON_DESKTOP_TOKEN", "a" * 64)
        monkeypatch.setenv("PRESENTON_DESKTOP_ORIGINS", "http://testserver")
        headers["x-presenton-desktop-token"] = "a" * 64
        assert client.get("/api/v1/ppt/ollama/models/available", params={"ollama_url": "http://127.0.0.1:11434"}).status_code == 401
    response = client.get("/api/v1/ppt/ollama/models/available", params={"ollama_url": "http://127.0.0.1:11434"}, headers=headers)
    assert response.status_code == 200
    models.assert_awaited_once_with("http://127.0.0.1:11434")
