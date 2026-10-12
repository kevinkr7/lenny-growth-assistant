import json

import httpx
import pytest
import respx

from app.config import settings
from app.errors import AppError
from app.providers import gemini_status, provider_status, stream_gemini, stream_ollama


@respx.mock
async def test_ollama_generation_routes_through_pi_and_preserves_model_selection(monkeypatch):
    cfg = settings()
    monkeypatch.setattr(cfg, "model_timeout_seconds", 5)
    frames = [
        json.dumps({"type": "delta", "text": "Hello "}),
        json.dumps({"type": "delta", "text": "[S1]"}),
        json.dumps({"type": "done"}),
    ]
    route = respx.post(f"{cfg.pi_agent_url}/generate").mock(return_value=httpx.Response(200, text="\n".join(frames)))
    assert "".join([t async for t in stream_ollama("system", "prompt")]) == "Hello [S1]"
    body = json.loads(route.calls[0].request.content)
    assert body["provider"] == "ollama" and body["model"] == cfg.ollama_model
    assert body["system"] == "system" and body["prompt"] == "prompt"
    assert body["ollama_base_url"] == cfg.ollama_base_url


@respx.mock
async def test_unavailable_ollama_is_actionable():
    respx.post(f"{settings().pi_agent_url}/generate").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(AppError, match="Pi Coding Agent service"):
        _ = [t async for t in stream_ollama("s", "p")]


@respx.mock
async def test_truncated_stream_is_not_success():
    respx.post(f"{settings().pi_agent_url}/generate").mock(
        return_value=httpx.Response(200, text='{"type":"delta","text":"Partial"}')
    )
    with pytest.raises(AppError, match="ended early"):
        _ = [t async for t in stream_ollama("s", "p")]


@respx.mock
async def test_status_distinguishes_model_missing(monkeypatch):
    monkeypatch.setattr(settings(), "gemini_api_key", "")
    respx.get(f"{settings().ollama_base_url}/api/tags").mock(return_value=httpx.Response(200, json={"models": []}))
    status = await provider_status()
    assert not status[0]["available"] and "ollama pull" in status[0]["message"]


async def test_missing_gemini_key_is_actionable(monkeypatch):
    monkeypatch.setattr(settings(), "gemini_api_key", "")
    with pytest.raises(AppError, match="GEMINI_API_KEY"):
        _ = [part async for part in stream_gemini("system", "question")]


@respx.mock
async def test_gemini_minimal_provider_smoke_contract_disables_tools(monkeypatch):
    from app.providers import stream_gemini

    cfg = settings()
    monkeypatch.setattr(cfg, "gemini_api_key", "private-gemini-test-key")
    route = respx.post(f"{cfg.pi_agent_url}/generate").mock(
        return_value=httpx.Response(200, text='{"type":"delta","text":"PASS"}\n{"type":"done"}\n')
    )
    assert "".join([part async for part in stream_gemini("system", "Reply with exactly PASS.")]) == "PASS"
    body = json.loads(route.calls[0].request.content)
    assert body["provider"] == "gemini" and body["model"] == cfg.gemini_model
    assert body["api_key"] == "private-gemini-test-key"
    assert body["timeout_ms"] == cfg.gemini_timeout_seconds * 1000
    assert body["system"] == "system" and body["prompt"] == "Reply with exactly PASS."


@respx.mock
async def test_gemini_rate_limit_error_is_sanitized(monkeypatch):
    monkeypatch.setattr(settings(), "gemini_api_key", "private-gemini-test-key")
    respx.post(f"{settings().pi_agent_url}/generate").mock(
        return_value=httpx.Response(200, text='{"type":"error","code":"gemini_rate_limit"}\n')
    )
    with pytest.raises(AppError) as caught:
        _ = [part async for part in stream_gemini("system", "question")]
    assert caught.value.code == "gemini_rate_limit"
    assert "private-gemini-test-key" not in caught.value.message


@respx.mock
async def test_gemini_provider_error_does_not_silently_switch_provider(monkeypatch):
    monkeypatch.setattr(settings(), "gemini_api_key", "private-gemini-test-key")
    route = respx.post(f"{settings().pi_agent_url}/generate").mock(
        return_value=httpx.Response(200, text='{"type":"error","code":"provider_error"}\n')
    )
    with pytest.raises(AppError, match="selected model"):
        _ = [part async for part in stream_gemini("system", "question")]
    assert route.call_count == 1
    assert json.loads(route.calls[0].request.content)["provider"] == "gemini"


async def test_gemini_status_uses_mocked_model_access_check(monkeypatch):
    import google.genai

    cfg = settings()
    monkeypatch.setattr(cfg, "gemini_api_key", "private-gemini-test-key")
    monkeypatch.setattr("app.providers._gemini_status_cache", None)
    seen = {}

    class Models:
        async def get(self, **kwargs):
            seen.update(kwargs)

    class Aio:
        models = Models()

        async def aclose(self):
            pass

    class Client:
        def __init__(self, **kwargs):
            seen["client"] = kwargs
            self.aio = Aio()

    monkeypatch.setattr(google.genai, "Client", Client)
    result = await gemini_status()
    assert result["available"] and result["state"] == "model_access_verified"
    assert seen["client"]["api_key"] == "private-gemini-test-key"
    assert seen["model"] == cfg.gemini_model
    assert "private-gemini-test-key" not in str(result)


async def test_gemini_timeout_is_classified(monkeypatch):
    from app.providers import generate

    async def timeout_stream(*args):
        raise TimeoutError("provider timeout")
        yield

    monkeypatch.setattr("app.providers.stream_gemini", timeout_stream)
    with pytest.raises(AppError) as caught:
        _ = [part async for part in generate("gemini", "system", "Reply with exactly PASS.")]
    assert caught.value.code == "gemini_timeout"


async def test_gemini_cancellation_propagates(monkeypatch):
    import asyncio

    from app.providers import generate

    async def cancelled_stream(*args):
        raise asyncio.CancelledError()
        yield

    monkeypatch.setattr("app.providers.stream_gemini", cancelled_stream)
    with pytest.raises(asyncio.CancelledError):
        _ = [part async for part in generate("gemini", "system", "Reply with exactly PASS.")]


async def test_planning_prefix_never_streams_to_user(monkeypatch):
    from app.providers import generate

    async def planning(*args):
        for part in ["I need to ", "analyze the evidence before answering. ", "Private planning. " * 8]:
            yield part

    monkeypatch.setattr("app.providers.stream_ollama", planning)
    visible = []
    with pytest.raises(AppError, match="planning text"):
        async for token in generate("ollama", "s", "p"):
            visible.append(token)
    assert visible == []
