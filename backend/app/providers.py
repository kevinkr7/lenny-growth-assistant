import asyncio
import json
import re
import time
from collections.abc import AsyncIterator

import httpx
import google.auth.transport.requests
import google.oauth2.id_token

from app.config import settings
from app.errors import AppError

_gemini_status_cache: tuple[tuple[str, str], float, dict] | None = None


def _gemini_exception_category(exc: Exception) -> tuple[int | None, str]:
    """Extract only safe SDK type/status metadata for error classification."""
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        status = getattr(exc, "code", None)
    if not isinstance(status, int):
        status = None
    return status, f"{type(exc).__name__} {getattr(exc, 'code', '')} {exc}"


def gemini_failure(status: int | None = None, category: str = "") -> AppError:
    category = category.lower()
    if status in {401, 403} or any(word in category for word in ["api key", "authentication", "permission", "unauthenticated"]):
        return AppError("gemini_authentication", "Gemini rejected the credentials. Check GEMINI_API_KEY and account access.", 401)
    if status == 429 or any(word in category for word in ["quota", "rate limit", "resource exhausted"]):
        return AppError("gemini_rate_limit", "Gemini is rate limited or out of quota. Wait briefly, then retry.", 429)
    if status == 404 or "not found" in category:
        return AppError("gemini_model_missing", "The configured Gemini model is unavailable. Check GEMINI_MODEL.", 404)
    if status == 400 or any(word in category for word in ["clienterror", "invalid_argument", "invalid argument", "bad request"]):
        return AppError("gemini_request", "Gemini rejected the request. Check the prompt and model configuration.", 400)
    if any(word in category for word in ["servererror", "internal", "service unavailable"]):
        return AppError("gemini_service", "Gemini reported a service error. Retry shortly or choose Ollama.", 503)
    if any(word in category for word in ["typeerror", "attributeerror", "validationerror"]):
        return AppError("gemini_sdk", "The Gemini SDK rejected the request shape. Restart the API after updating dependencies.", 503)
    if any(word in category for word in ["safety", "blocked", "finish_reason"]):
        return AppError("gemini_safety", "Gemini blocked this request or returned no usable text. Try a different prompt.", 422)
    if "timeout" in category or "timed out" in category:
        return AppError("gemini_timeout", "Gemini timed out. Check the network and retry.", 504)
    if any(word in category for word in ["connect", "network", "unavailable", "service"]):
        return AppError("gemini_network", "Cannot reach Gemini. Check the network and retry.", 503)
    return AppError("gemini_error", "Gemini could not complete the request. Check its model and service status.", 503)


async def gemini_status() -> dict:
    global _gemini_status_cache
    cfg = settings()
    status = {
        "id": "gemini",
        "name": "Gemini",
        "model": cfg.gemini_model,
        "configured": bool(cfg.gemini_api_key),
        "available": False,
        "verified": False,
        "state": "unconfigured",
        "message": "Gemini isn't configured. Set GEMINI_API_KEY in the local .env and restart the API.",
    }
    if not cfg.gemini_api_key:
        return status
    cache_key = (cfg.gemini_api_key, cfg.gemini_model)
    if _gemini_status_cache and _gemini_status_cache[0] == cache_key and time.monotonic() < _gemini_status_cache[1]:
        return dict(_gemini_status_cache[2])
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(
            api_key=cfg.gemini_api_key,
            http_options=types.HttpOptions(timeout=5000),
        )
        try:
            await client.aio.models.get(model=cfg.gemini_model)
        finally:
            await client.aio.aclose()
        status.update(
            available=True,
            state="model_access_verified",
            message="Credentials and model access checked; generation is checked when used.",
        )
    except Exception as exc:  # SDK exception types vary by google-genai release; sanitize through classifier.
        error_status, category = _gemini_exception_category(exc)
        error = gemini_failure(status=error_status, category=category)
        status.update(state=error.code, message=error.message)
    _gemini_status_cache = (cache_key, time.monotonic() + 60, status)
    return dict(status)


def model_name(provider: str) -> str:
    cfg = settings()
    return {"ollama": cfg.ollama_model, "gemini": cfg.gemini_model}.get(provider, "")


async def provider_status() -> list[dict]:
    cfg = settings()
    local = {
        "id": "ollama",
        "name": "Ollama",
        "model": cfg.ollama_model,
        "available": False,
        "verified": False,
        "state": "unavailable",
        "message": "Local model unavailable. Start Ollama and retry connection.",
    }
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(f"{cfg.ollama_base_url}/api/tags")
            response.raise_for_status()
            names = [m["name"] for m in response.json().get("models", [])]
            local["available"] = cfg.ollama_model in names or cfg.ollama_model + ":latest" in names
            local["verified"] = local["available"]
            local["state"] = "server_and_model_verified" if local["available"] else "model_missing"
            local["message"] = (
                "Local server reachable and model installed"
                if local["available"]
                else f"Download the local model: ollama pull {cfg.ollama_model}"
            )
    except (httpx.HTTPError, ValueError, KeyError):
        pass
    return [local, await gemini_status()]


def pi_failure(provider: str, code: str) -> AppError:
    messages = {
        "provider_timeout": ("model_timeout", "The selected model took too long to respond. Retry the request." , 504),
        "request_cancelled": ("request_cancelled", "The model request was cancelled.", 499),
        "model_missing": ("model_missing", "The selected provider does not have the configured model available.", 404),
        "gemini_not_configured": ("gemini_not_configured", "Gemini isn't configured. Set GEMINI_API_KEY or choose another provider.", 503),
        "gemini_authentication": ("gemini_authentication", "Gemini rejected the credentials. Check GEMINI_API_KEY and account access.", 401),
        "gemini_rate_limit": ("gemini_rate_limit", "Gemini is rate limited or out of quota. Wait briefly, then retry.", 429),
        "gemini_service": ("gemini_service", "Gemini reported a service error. Retry shortly or choose Ollama.", 503),
        "ollama_unavailable": ("ollama_unavailable", "Local model unavailable. Start Ollama and retry connection.", 503),
        "provider_empty": ("provider_empty", "The selected model returned no usable text. Retry the request.", 502),
        "provider_configuration": ("provider_configuration", "The selected provider is not configured correctly.", 500),
        "pi_agent_unavailable": ("pi_agent_unavailable", "Cannot reach the Pi Coding Agent service. Start it and retry.", 503),
        "provider_error": ("provider_error", "The selected model could not complete the request. Check provider status and retry.", 502),
    }
    if code == "provider_timeout" and provider == "gemini":
        return gemini_failure(category="timeout")
    error_code, message, status = messages.get(code, messages["provider_error"])
    return AppError(error_code, message, status)


async def stream_pi_agent(provider: str, system: str, prompt: str) -> AsyncIterator[str]:
    """Use the Pi Coding Agent SDK bridge for every model generation call."""
    cfg = settings()
    if provider == "gemini" and not cfg.gemini_api_key:
        raise AppError("gemini_not_configured", "Gemini isn't configured. Set GEMINI_API_KEY or choose another provider.")
    timeout_seconds = cfg.gemini_timeout_seconds if provider == "gemini" else cfg.model_timeout_seconds
    request = {
        "provider": provider,
        "model": model_name(provider),
        "system": system,
        "prompt": prompt,
        "timeout_ms": timeout_seconds * 1000,
        "ollama_base_url": cfg.ollama_base_url,
        "max_tokens": cfg.model_max_tokens,
        "context_tokens": cfg.ollama_context_tokens,
    }
    if provider == "gemini":
        request["api_key"] = cfg.gemini_api_key
    completed = False
    timeout = httpx.Timeout(timeout_seconds, connect=5, read=timeout_seconds)
    try:
        auth_request = google.auth.transport.requests.Request()
        identity_token = google.oauth2.id_token.fetch_id_token(
            auth_request, cfg.pi_agent_url.rstrip("/")
        )
        headers = {"Authorization": f"Bearer {identity_token}"}
        async with asyncio.timeout(timeout_seconds):
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    f"{cfg.pi_agent_url.rstrip('/')}/generate",
                    json=request,
                    headers=headers,
                ) as response:
                    if response.status_code >= 400:
                        try:
                            frame = json.loads((await response.aread()).decode("utf-8"))
                        except (ValueError, UnicodeDecodeError):
                            frame = {}
                        raise pi_failure(provider, str(frame.get("code", "provider_error")))
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        try:
                            frame = json.loads(line)
                        except json.JSONDecodeError as exc:
                            raise AppError("stream_invalid", "The model stream returned an invalid frame. Retry.", 502) from exc
                        if frame.get("type") == "delta" and isinstance(frame.get("text"), str):
                            yield frame["text"]
                        elif frame.get("type") == "error":
                            raise pi_failure(provider, str(frame.get("code", "provider_error")))
                        elif frame.get("type") == "done":
                            completed = True
                    if not completed:
                        raise AppError("stream_incomplete", "The model connection ended early. Please retry.", 502)
    except asyncio.CancelledError:
        raise
    except TimeoutError as exc:
        if provider == "gemini":
            raise gemini_failure(category="timeout") from exc
        raise AppError("model_timeout", "The selected model took too long to respond. Retry the request.", 504) from exc
    except httpx.TimeoutException as exc:
        if provider == "gemini":
            raise gemini_failure(category="timeout") from exc
        raise AppError("model_timeout", "The selected model took too long to respond. Retry the request.", 504) from exc
    except httpx.HTTPError as exc:
        if provider == "gemini":
            raise AppError("pi_agent_unavailable", "Cannot reach the Pi Coding Agent service. Start it and retry.", 503) from exc
        raise AppError("pi_agent_unavailable", "Cannot reach the Pi Coding Agent service. Start it and retry.", 503) from exc


async def stream_ollama(system: str, prompt: str) -> AsyncIterator[str]:
    async for text in stream_pi_agent("ollama", system, prompt):
        yield text


async def stream_gemini(system: str, prompt: str) -> AsyncIterator[str]:
    async for text in stream_pi_agent("gemini", system, prompt):
        yield text


async def generate(provider: str, system: str, prompt: str) -> AsyncIterator[str]:
    try:
        async with asyncio.timeout(settings().model_timeout_seconds):
            count = 0
            prefix = ""
            prefix_checked = False
            if provider == "ollama":
                stream = stream_ollama(system, prompt)
            elif provider == "gemini":
                stream = stream_gemini(system, prompt)
            else:
                raise AppError("provider_unknown", "Unknown provider selected. Choose Ollama or Gemini.", 400)
            async for token in stream:
                count += len(token)
                if count > 45000:
                    raise AppError("output_limit", "The response was too long. Try a more focused request.")
                if not prefix_checked:
                    prefix += token
                    if len(prefix) < 100:
                        continue
                    reject_planning_prefix(prefix)
                    yield prefix
                    prefix_checked = True
                else:
                    yield token
            if not prefix_checked and prefix:
                reject_planning_prefix(prefix)
                yield prefix
    except TimeoutError as exc:
        if provider == "gemini":
            raise gemini_failure(category="timeout") from exc
        raise AppError(
            "model_timeout", "The model took too long to respond. Try again or use a smaller model."
        ) from exc


def reject_planning_prefix(text: str):
    if re.match(
        r"\s*(?:<think>|I need to (?:analy[sz]e|think|reason)|Let me (?:think|analy[sz]e)|We need to (?:analy[sz]e|think))",
        text,
        re.I,
    ):
        raise AppError(
            "model_format",
            "The local model returned planning text instead of an answer. Use the documented instruct model and retry.",
        )
