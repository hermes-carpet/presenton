import asyncio
import json

import pytest
from fastapi import HTTPException

from utils import ollama


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    calls = []

    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def json(self, **_kwargs):
            return {"models": [{"name": "custom-local:latest", "size": 42}]}

        async def text(self):
            return "Control upstream error"

        @property
        def content(self):
            async def lines():
                for item in [
                    {"status": "starting"},
                    {"status": "downloading", "total": 100, "completed": 25},
                    {"status": "success"},
                ]:
                    yield json.dumps(item).encode() + b"\n"
            return lines()

    response = Response()

    class Session:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def get(self, url, **kwargs):
            calls.append(("GET", url, kwargs))
            return response

        def post(self, url, **kwargs):
            calls.append(("POST", url, kwargs))
            return response

    monkeypatch.setattr(ollama.aiohttp, "ClientSession", Session)
    return calls, response


async def collect_pull(url):
    return [frame async for frame in ollama.pull_ollama_model("custom-local:latest", url)]


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/admin/action?x=", "http://[::1]/admin/action#ignored",
    "http://localhost:11434/proxy?next=http://169.254.169.254/", "file:///tmp/ollama",
    "http://localhost:99999", "http://localhost:invalid", "http://local\thost:11434",
    "http://localhost:11434/proxy\\admin", "http://localhost:11434/proxy/../admin",
    "http://localhost:11434/proxy/%2e%2e/admin", "http://localhost:11434/proxy/%252e%252e/admin",
    "http://localhost:11434/proxy%2f..%2fadmin", "http://localhost:11434/proxy/%0aadmin",
    "http://localhost:11434/proxy/%5cadmin", "http://localhost:11434/proxy/%3fadmin",
    "http://localhost:11434/proxy/%23admin", "http://localhost:11434/proxy\x7f",
])
@pytest.mark.parametrize("operation", ["list", "pull"])
def test_invalid_bases_never_reach_either_transport(transport, url, operation):
    calls, _ = transport
    with pytest.raises(HTTPException) as error:
        asyncio.run(ollama.list_available_ollama_models(url) if operation == "list" else collect_pull(url))
    assert error.value.status_code == 400
    assert calls == []


@pytest.mark.parametrize("url, expected", [
    (None, "http://localhost:11434"),
    ("http://host.docker.internal:11434/", "http://host.docker.internal:11434"),
    ("http://192.168.0.10:11434/team/ollama", "http://192.168.0.10:11434/team/ollama"),
    ("http://[::1]:11434/proxy", "http://[::1]:11434/proxy"),
    ("http://[fe80::1%en0]:11434/proxy", "http://[fe80::1%en0]:11434/proxy"),
    ("http://[fe80::1%25en0]:11434/proxy", "http://[fe80::1%25en0]:11434/proxy"),
    ("https://ollama.example/proxy/v1.0/", "https://ollama.example/proxy/v1.0"),
    ("ollama.example/proxy", "http://ollama.example/proxy"),
    ("http://ollama.example/my%20proxy", "http://ollama.example/my%20proxy"),
    ("https://b\u00fccher.example/\u63a5\u53e3", "https://xn--bcher-kva.example/%E6%8E%A5%E5%8F%A3"),
    ("https://user:password@ollama.example/proxy", "https://user:password@ollama.example/proxy"),
    ("https://user:p%40ss@ollama.example/proxy", "https://user:p%40ss@ollama.example/proxy"),
])
def test_list_preserves_local_and_unsaved_proxy_roots(transport, url, expected):
    calls, _ = transport
    models = asyncio.run(ollama.list_available_ollama_models(url))
    assert [model.name for model in models] == ["custom-local:latest"]
    assert calls == [("GET", expected + "/api/tags", {"allow_redirects": False})]


def test_saved_proxy_is_used_without_request_override(monkeypatch, transport):
    calls, _ = transport
    monkeypatch.setenv("OLLAMA_URL", "http://ollama.internal:11434/private/proxy/")
    asyncio.run(ollama.list_available_ollama_models())
    assert calls == [("GET", "http://ollama.internal:11434/private/proxy/api/tags", {"allow_redirects": False})]


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_list_does_not_follow_redirects(transport, status):
    calls, response = transport
    response.status = status
    with pytest.raises(HTTPException) as error:
        asyncio.run(ollama.list_available_ollama_models("http://localhost:11434/proxy"))
    assert error.value.status_code == 502
    assert len(calls) == 1
    assert calls[0][2]["allow_redirects"] is False


def test_pull_preserves_body_and_stream_frames(transport):
    calls, _ = transport
    frames = asyncio.run(collect_pull("http://192.168.0.10:11434/team/ollama"))
    events = [json.loads(frame.split("data: ", 1)[1]) for frame in frames]
    assert [event["type"] for event in events] == ["status", "progress", "complete"]
    assert events[1]["progress"] == 25.0
    assert events[2]["model"] == "custom-local:latest"
    assert calls == [("POST", "http://192.168.0.10:11434/team/ollama/api/pull", {
        "json": {"name": "custom-local:latest", "stream": True, "insecure": False},
        "allow_redirects": False,
    })]


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_pull_redirect_is_an_error_frame_without_replay(transport, status):
    calls, response = transport
    response.status = status
    frames = asyncio.run(collect_pull("http://localhost:11434/proxy"))
    assert len(frames) == 1
    assert frames[0].startswith("event: error\n")
    assert "redirect" in json.loads(frames[0].split("data: ", 1)[1])["detail"].lower()
    assert len(calls) == 1
    assert calls[0][2]["allow_redirects"] is False
