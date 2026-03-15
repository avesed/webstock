"""LLM proxy endpoint -- transparent forwarding to ai-gateway.

This is a simple pass-through proxy that reads the prediction provider
from SettingsCache and forwards the request body to ai-gateway's
/v1/chat/completions endpoint. It does NOT parse or transform the
request/response -- just injects the correct headers.

Used by RD-Agent subprocess which cannot directly access ai-gateway
credentials or provider routing.
"""

import asyncio
import json
import logging
from typing import AsyncIterator

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.config import get_settings
from app.core.settings_cache import settings_cache

logger = logging.getLogger(__name__)

router = APIRouter(tags=["llm"])

# Shared httpx client -- created lazily, reused across requests
_client: httpx.AsyncClient | None = None

_LLM_TIMEOUT = 120.0  # seconds

# Strong references to fire-and-forget tasks (prevent GC before completion)
_background_tasks: set[asyncio.Task[None]] = set()


def _get_client() -> httpx.AsyncClient:
    """Get or create the shared httpx async client."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(_LLM_TIMEOUT, connect=10.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=5),
        )
    return _client


async def close_llm_client() -> None:
    """Close the shared httpx client on shutdown."""
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


async def _stream_response(resp: httpx.Response) -> AsyncIterator[bytes]:
    """Stream bytes from the upstream response."""
    try:
        async for chunk in resp.aiter_bytes():
            yield chunk
    finally:
        await resp.aclose()


@router.post("/v1/llm/chat/completions", response_model=None)
async def llm_proxy(request: Request) -> StreamingResponse | JSONResponse:
    """Forward chat completion requests to ai-gateway.

    Reads the configured prediction LLM provider from the settings cache
    and injects X-Provider-Id and X-Internal-Token headers before
    forwarding to ai-gateway's /v1/chat/completions endpoint.

    Supports both streaming (SSE) and non-streaming modes.
    """
    settings = get_settings()

    # Resolve prediction LLM provider
    llm_config = await settings_cache.get_llm_config()
    if not llm_config.provider_id:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Prediction LLM provider not configured. "
                "Set prediction_provider_id in system_settings."
            },
        )

    # Read raw request body
    body = await request.body()

    # Detect stream mode from request JSON
    stream = False
    try:
        payload = json.loads(body)
        stream = payload.get("stream", False)
    except (json.JSONDecodeError, TypeError):
        pass

    # Build upstream URL
    gateway_url = settings.AI_GATEWAY_URL.rstrip("/")
    upstream_url = f"{gateway_url}/v1/chat/completions"

    # Build headers for ai-gateway
    forward_headers = {
        "Content-Type": "application/json",
        "X-Provider-Id": str(llm_config.provider_id),
    }
    if settings.INTERNAL_API_TOKEN:
        forward_headers["X-Internal-Token"] = settings.INTERNAL_API_TOKEN

    # Forward X-Request-ID if present
    request_id = request.headers.get("x-request-id")
    if request_id:
        forward_headers["X-Request-ID"] = request_id

    client = _get_client()

    try:
        if stream:
            # Streaming mode: forward SSE response
            resp = await client.send(
                client.build_request(
                    "POST",
                    upstream_url,
                    content=body,
                    headers=forward_headers,
                ),
                stream=True,
            )

            if resp.status_code != 200:
                error_body = await resp.aread()
                await resp.aclose()
                logger.warning(
                    "AI gateway returned %d for streaming request: %s",
                    resp.status_code,
                    error_body[:500],
                )
                return JSONResponse(
                    status_code=resp.status_code,
                    content={"detail": f"AI gateway error: {resp.status_code}"},
                )

            return StreamingResponse(
                _stream_response(resp),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )
        else:
            # Non-streaming mode: forward and return JSON
            resp = await client.post(
                upstream_url,
                content=body,
                headers=forward_headers,
            )

            if resp.status_code != 200:
                logger.warning(
                    "AI gateway returned %d: %s",
                    resp.status_code,
                    resp.text[:500],
                )
                return JSONResponse(
                    status_code=resp.status_code,
                    content={"detail": f"AI gateway error: {resp.status_code}"},
                )

            resp_data = resp.json()

            # Fire-and-forget usage recording
            task = asyncio.create_task(
                _record_proxy_usage(resp_data)
            )
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

            return JSONResponse(
                status_code=200,
                content=resp_data,
            )

    except httpx.TimeoutException:
        logger.error("AI gateway request timed out after %.0fs", _LLM_TIMEOUT)
        return JSONResponse(
            status_code=504,
            content={"detail": "AI gateway request timed out"},
        )
    except httpx.ConnectError as e:
        logger.error("Cannot connect to AI gateway at %s: %s", gateway_url, e)
        return JSONResponse(
            status_code=502,
            content={"detail": "Cannot connect to AI gateway"},
        )
    except Exception as e:
        logger.exception("Unexpected error proxying to AI gateway: %s", e)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal error in LLM proxy"},
        )


async def _record_proxy_usage(resp_data: dict) -> None:
    """Extract usage from OpenAI-format response and record it."""
    try:
        from app.core.usage_recorder import record_usage

        usage = resp_data.get("usage", {})
        if not usage:
            return

        model = resp_data.get("model", "unknown")
        cached = 0
        details = usage.get("prompt_tokens_details")
        if isinstance(details, dict):
            cached = details.get("cached_tokens", 0)

        await record_usage(
            purpose="rdagent",
            model=model,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            cached_tokens=cached,
        )
    except Exception as e:
        logger.debug("Proxy usage recording failed: %s", e)
