"""AI Analysis API endpoints."""

import asyncio
import json
import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import AgentType, create_orchestrator
from app.core.rate_limiter import rate_limit
from app.core.security import get_current_user
from app.core.user_ai_config import UserAIConfig, current_user_ai_config
from app.db.database import get_db
from app.models.user import User
from app.models.user_settings import UserSettings
from app.schemas.analysis import (
    AgentResultResponse,
    AgentTypeEnum,
    AnalysisErrorResponse,
    FullAnalysisResponse,
    SingleAnalysisResponse,
)
from app.services.stock_service import detect_market
from app.utils.symbol_validation import validate_symbol

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analysis", tags=["Analysis"])

# Rate limiting: 10 requests per minute for AI analysis per user
AI_ANALYSIS_RATE_LIMIT = rate_limit(
    max_requests=10,
    window_seconds=60,
    key_prefix="ai_analysis",
)

async def apply_user_ai_config(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Load AI settings using SettingsService and set them in the request context.

    Priority: user settings (if permitted) > system settings > env variables.
    """
    from app.services.settings_service import get_settings_service

    try:
        settings_service = get_settings_service()
        ai_config = await settings_service.get_user_ai_config(db, current_user.id)

        # Set resolved config in context for OpenAI client to use
        current_user_ai_config.set(
            UserAIConfig(
                api_key=ai_config.api_key,
                base_url=ai_config.base_url,
                model=ai_config.model,
                max_tokens=ai_config.max_tokens,
                temperature=ai_config.temperature,
                system_prompt=ai_config.system_prompt,
            )
        )
    except Exception as e:
        logger.warning(f"Failed to load user AI config: {e}")


@router.get(
    "/{symbol}",
    response_model=FullAnalysisResponse,
    responses={
        400: {"model": AnalysisErrorResponse, "description": "Invalid symbol"},
        429: {"model": AnalysisErrorResponse, "description": "Rate limit exceeded"},
        503: {"model": AnalysisErrorResponse, "description": "Service unavailable"},
    },
    summary="Full AI analysis",
    description="Get comprehensive AI analysis from all agents (fundamental, technical, sentiment).",
)
async def get_full_analysis(
    symbol: str,
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(AI_ANALYSIS_RATE_LIMIT),
    _ai_config: None = Depends(apply_user_ai_config),
):
    """
    Get full AI analysis for a stock.

    Runs all three analysis agents in parallel:
    - Fundamental analysis (valuation, financials, business)
    - Technical analysis (trends, indicators, levels)
    - Sentiment analysis (momentum, volume, market context)

    **Rate Limit**: 10 requests per minute per user

    **Note**: This endpoint may take 10-30 seconds as it runs multiple AI analyses.
    For real-time updates, use the streaming endpoint instead.
    """
    symbol = validate_symbol(symbol)
    market = detect_market(symbol)

    logger.info(f"Full analysis requested for {symbol} by user {current_user.id}")

    try:
        orchestrator = await create_orchestrator()
        result = await orchestrator.analyze(symbol, market.value)

        # Dispatch embedding for RAG search (best-effort)
        try:
            from worker.tasks.embedding_tasks import embed_analysis_report
            for a_type, a_result in result.results.items():
                if a_result.success and a_result.content:
                    embed_analysis_report.delay({
                        "source_id": f"analysis-{symbol}-{a_type}-{int(time.time())}",
                        "symbol": symbol,
                        "agent_type": a_type,
                        "content": a_result.content,
                    })
        except Exception as embed_err:
            logger.warning("Failed to dispatch analysis embedding: %s", embed_err)

        return FullAnalysisResponse(
            symbol=result.symbol,
            market=result.market,
            results={
                agent_type: AgentResultResponse(
                    agent_type=AgentTypeEnum(agent_result.agent_type.value),
                    symbol=agent_result.symbol,
                    market=agent_result.market,
                    success=agent_result.success,
                    content=agent_result.content,
                    structured_data=agent_result.structured_data,
                    error=agent_result.error,
                    tokens_used=agent_result.tokens_used,
                    latency_ms=agent_result.latency_ms,
                    timestamp=agent_result.timestamp,
                )
                for agent_type, agent_result in result.results.items()
            },
            total_tokens=result.total_tokens,
            total_latency_ms=result.total_latency_ms,
            timestamp=result.timestamp,
            summary=result.to_dict()["summary"],
        )

    except Exception as e:
        logger.exception(f"Full analysis error for {symbol}: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Analysis service temporarily unavailable. Please try again later.",
        )


# Streaming configuration
STREAMING_TIMEOUT_SECONDS = 300  # 5 minutes max for streaming
HEARTBEAT_INTERVAL_SECONDS = 15


@router.get(
    "/{symbol}/stream",
    responses={
        400: {"model": AnalysisErrorResponse, "description": "Invalid symbol"},
        429: {"model": AnalysisErrorResponse, "description": "Rate limit exceeded"},
    },
    summary="Streaming AI analysis",
    description="Get streaming AI analysis with Server-Sent Events (SSE).",
)
async def get_streaming_analysis(
    request: Request,
    symbol: str,
    language: str = Query("en", description="Language for analysis output (en or zh)"),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(AI_ANALYSIS_RATE_LIMIT),
    _ai_config: None = Depends(apply_user_ai_config),
):
    """
    Get streaming AI analysis for a stock using Server-Sent Events (SSE).

    Returns a stream of events as the analysis progresses:
    - `start`: Analysis started
    - `agent_start`: Individual agent started
    - `agent_chunk`: Partial content from an agent
    - `agent_complete`: Agent finished with structured data
    - `agent_error`: Agent encountered an error
    - `complete`: All agents finished
    - `heartbeat`: Keep-alive signal

    **Rate Limit**: 10 requests per minute per user

    **Event Format**:
    ```
    data: {"type": "agent_chunk", "agent": "fundamental", "content": "...", "timestamp": 1234567890}

    ```
    """
    symbol = validate_symbol(symbol)
    market = detect_market(symbol)
    # Normalize language to 'en' or 'zh'
    lang = "zh" if language.lower().startswith("zh") else "en"

    logger.info(f"Streaming analysis requested for {symbol} by user {current_user.id} (lang={lang})")

    async def event_generator():
        """Generate SSE events with heartbeat and proper cleanup."""
        start_time = time.time()
        last_event_time = time.time()
        heartbeat_task = None
        analysis_complete = False

        # Queue for heartbeat events
        heartbeat_queue: asyncio.Queue = asyncio.Queue()

        async def heartbeat_sender():
            """Send periodic heartbeat events."""
            nonlocal last_event_time
            try:
                while not analysis_complete:
                    await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
                    if not analysis_complete:
                        # Check if we need a heartbeat (no recent events)
                        if time.time() - last_event_time >= HEARTBEAT_INTERVAL_SECONDS:
                            await heartbeat_queue.put(
                                f'data: {json.dumps({"type": "heartbeat", "timestamp": time.time()})}\n\n'
                            )
            except asyncio.CancelledError:
                pass

        try:
            orchestrator = await create_orchestrator()

            # Start heartbeat task
            heartbeat_task = asyncio.create_task(heartbeat_sender())

            # Stream analysis events with timeout
            async def stream_with_timeout():
                async for event in orchestrator.analyze_stream(symbol, market.value, lang):
                    yield event

            try:
                async with asyncio.timeout(STREAMING_TIMEOUT_SECONDS):
                    async for event in stream_with_timeout():
                        # Check for client disconnect
                        if await request.is_disconnected():
                            logger.info(f"Client disconnected during streaming for {symbol}")
                            break

                        # Check for overall timeout
                        if time.time() - start_time > STREAMING_TIMEOUT_SECONDS:
                            logger.warning(f"Streaming timeout reached for {symbol}")
                            yield f'data: {json.dumps({"type": "timeout", "message": "Analysis timeout reached", "timestamp": time.time()})}\n\n'
                            break

                        # Yield the analysis event
                        yield event
                        last_event_time = time.time()

                        # Also yield any pending heartbeat events
                        while not heartbeat_queue.empty():
                            try:
                                hb_event = heartbeat_queue.get_nowait()
                                yield hb_event
                                last_event_time = time.time()
                            except asyncio.QueueEmpty:
                                break

            except asyncio.TimeoutError:
                logger.warning(f"Streaming analysis timeout for {symbol}")
                yield f'data: {json.dumps({"type": "timeout", "message": "Analysis timeout reached", "timestamp": time.time()})}\n\n'

        except Exception as e:
            logger.exception(f"Streaming analysis error for {symbol}: {e}")
            # Return generic error message to client, detailed error is logged
            yield f'data: {json.dumps({"type": "error", "error": "Analysis service error. Please try again later.", "timestamp": time.time()})}\n\n'

        finally:
            # Cleanup
            analysis_complete = True
            if heartbeat_task and not heartbeat_task.done():
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
            logger.debug(f"Streaming cleanup completed for {symbol}")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


@router.get(
    "/{symbol}/fundamental",
    response_model=SingleAnalysisResponse,
    responses={
        400: {"model": AnalysisErrorResponse, "description": "Invalid symbol"},
        429: {"model": AnalysisErrorResponse, "description": "Rate limit exceeded"},
        503: {"model": AnalysisErrorResponse, "description": "Service unavailable"},
    },
    summary="Fundamental analysis only",
    description="Get fundamental analysis (valuation, financials, business metrics).",
)
async def get_fundamental_analysis(
    symbol: str,
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(AI_ANALYSIS_RATE_LIMIT),
    _ai_config: None = Depends(apply_user_ai_config),
):
    """
    Get fundamental analysis for a stock.

    Analyzes:
    - Valuation metrics (P/E, P/B, EV/EBITDA)
    - Profitability (margins, ROE, ROA)
    - Growth (revenue, earnings)
    - Balance sheet health
    - Dividend analysis

    **Rate Limit**: 10 requests per minute per user
    """
    symbol = validate_symbol(symbol)
    market = detect_market(symbol)

    logger.info(f"Fundamental analysis requested for {symbol} by user {current_user.id}")

    try:
        orchestrator = await create_orchestrator()
        result = await orchestrator.analyze_single(symbol, market.value, AgentType.FUNDAMENTAL)

        return SingleAnalysisResponse(
            symbol=result.symbol,
            market=result.market,
            agent_type=AgentTypeEnum.FUNDAMENTAL,
            success=result.success,
            content=result.content,
            structured_data=result.structured_data,
            error=result.error,
            tokens_used=result.tokens_used,
            latency_ms=result.latency_ms,
            timestamp=result.timestamp,
        )

    except Exception as e:
        logger.exception(f"Fundamental analysis error for {symbol}: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Analysis service temporarily unavailable. Please try again later.",
        )


@router.get(
    "/{symbol}/technical",
    response_model=SingleAnalysisResponse,
    responses={
        400: {"model": AnalysisErrorResponse, "description": "Invalid symbol"},
        429: {"model": AnalysisErrorResponse, "description": "Rate limit exceeded"},
        503: {"model": AnalysisErrorResponse, "description": "Service unavailable"},
    },
    summary="Technical analysis only",
    description="Get technical analysis (trends, indicators, support/resistance).",
)
async def get_technical_analysis(
    symbol: str,
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(AI_ANALYSIS_RATE_LIMIT),
    _ai_config: None = Depends(apply_user_ai_config),
):
    """
    Get technical analysis for a stock.

    Analyzes:
    - Price trends (short, medium, long term)
    - Support and resistance levels
    - Moving averages (SMA, EMA)
    - Momentum indicators (RSI, MACD)
    - Volume patterns
    - Chart patterns

    **Rate Limit**: 10 requests per minute per user
    """
    symbol = validate_symbol(symbol)
    market = detect_market(symbol)

    logger.info(f"Technical analysis requested for {symbol} by user {current_user.id}")

    try:
        orchestrator = await create_orchestrator()
        result = await orchestrator.analyze_single(symbol, market.value, AgentType.TECHNICAL)

        return SingleAnalysisResponse(
            symbol=result.symbol,
            market=result.market,
            agent_type=AgentTypeEnum.TECHNICAL,
            success=result.success,
            content=result.content,
            structured_data=result.structured_data,
            error=result.error,
            tokens_used=result.tokens_used,
            latency_ms=result.latency_ms,
            timestamp=result.timestamp,
        )

    except Exception as e:
        logger.exception(f"Technical analysis error for {symbol}: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Analysis service temporarily unavailable. Please try again later.",
        )


@router.get(
    "/{symbol}/sentiment",
    response_model=SingleAnalysisResponse,
    responses={
        400: {"model": AnalysisErrorResponse, "description": "Invalid symbol"},
        429: {"model": AnalysisErrorResponse, "description": "Rate limit exceeded"},
        503: {"model": AnalysisErrorResponse, "description": "Service unavailable"},
    },
    summary="Sentiment analysis only",
    description="Get sentiment analysis (momentum, volume patterns, market context).",
)
async def get_sentiment_analysis(
    symbol: str,
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(AI_ANALYSIS_RATE_LIMIT),
    _ai_config: None = Depends(apply_user_ai_config),
):
    """
    Get sentiment analysis for a stock.

    Analyzes:
    - Price momentum
    - Volume patterns (accumulation/distribution)
    - News sentiment (when available)
    - Market context
    - Relative strength
    - Potential catalysts

    **Rate Limit**: 10 requests per minute per user
    """
    symbol = validate_symbol(symbol)
    market = detect_market(symbol)

    logger.info(f"Sentiment analysis requested for {symbol} by user {current_user.id}")

    try:
        orchestrator = await create_orchestrator()
        result = await orchestrator.analyze_single(symbol, market.value, AgentType.SENTIMENT)

        return SingleAnalysisResponse(
            symbol=result.symbol,
            market=result.market,
            agent_type=AgentTypeEnum.SENTIMENT,
            success=result.success,
            content=result.content,
            structured_data=result.structured_data,
            error=result.error,
            tokens_used=result.tokens_used,
            latency_ms=result.latency_ms,
            timestamp=result.timestamp,
        )

    except Exception as e:
        logger.exception(f"Sentiment analysis error for {symbol}: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Analysis service temporarily unavailable. Please try again later.",
        )


@router.get(
    "/status",
    summary="Analysis service status",
    description="Get the status of the analysis service including rate limits and circuit breaker state.",
)
async def get_analysis_status(
    current_user: User = Depends(get_current_user),
):
    """
    Get analysis service status.

    Returns:
    - Rate limiter status (available tokens)
    - Circuit breaker status (state, stats)
    - Available agents
    """
    try:
        orchestrator = await create_orchestrator()
        status_info = await orchestrator.get_status()
        return {"status": "healthy", **status_info}
    except Exception as e:
        logger.exception(f"Status check error: {e}")
        # Return generic error message to client, detailed error is logged
        return {"status": "degraded", "error": "Service status check failed"}
