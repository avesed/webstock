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

from app.agents.langgraph import run_analysis, run_single_agent, stream_analysis
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


def _transform_langgraph_result_to_response(
    result: dict,
    symbol: str,
    market: str,
) -> FullAnalysisResponse:
    """Transform LangGraph AnalysisState to FullAnalysisResponse for backward compatibility."""
    from app.schemas.analysis import AnalysisSummary

    agent_results = {}
    total_tokens = 0
    total_latency = 0
    successful_agents = []
    failed_agents = []

    # Map agent types (LangGraph uses 4 agents, old API uses 3 + news in synthesis)
    agent_type_map = {
        "fundamental": AgentTypeEnum.FUNDAMENTAL,
        "technical": AgentTypeEnum.TECHNICAL,
        "sentiment": AgentTypeEnum.SENTIMENT,
    }

    for agent_type in ["fundamental", "technical", "sentiment"]:
        agent_result = result.get(agent_type)
        if agent_result:
            tokens = getattr(agent_result, 'tokens_used', 0) or 0
            latency = getattr(agent_result, 'latency_ms', 0) or 0
            total_tokens += tokens
            total_latency += latency

            if agent_result.success:
                successful_agents.append(agent_type)
            else:
                failed_agents.append(agent_type)

            # Get structured data if available
            structured_data = None
            if hasattr(agent_result, 'structured_data') and agent_result.structured_data:
                structured_data = agent_result.structured_data
            elif hasattr(agent_result, 'fundamental') and agent_result.fundamental:
                structured_data = agent_result.fundamental.model_dump() if hasattr(agent_result.fundamental, 'model_dump') else None
            elif hasattr(agent_result, 'technical') and agent_result.technical:
                structured_data = agent_result.technical.model_dump() if hasattr(agent_result.technical, 'model_dump') else None
            elif hasattr(agent_result, 'sentiment') and agent_result.sentiment:
                structured_data = agent_result.sentiment.model_dump() if hasattr(agent_result.sentiment, 'model_dump') else None

            agent_results[agent_type] = AgentResultResponse(
                agent_type=agent_type_map[agent_type],
                symbol=symbol,
                market=market,
                success=agent_result.success,
                content=getattr(agent_result, 'raw_content', None) or getattr(agent_result, 'content', None),
                structured_data=structured_data,
                error=agent_result.error if not agent_result.success else None,
                tokens_used=tokens,
                latency_ms=latency,
                timestamp=time.time(),
            )

    # Build recommendations summary from synthesis
    synthesis_output = result.get("synthesis_output", "")
    recommendations = {
        "synthesis": synthesis_output[:500] if synthesis_output else None,
        "clarification_rounds": result.get("clarification_round", 0),
    }

    return FullAnalysisResponse(
        symbol=symbol,
        market=market,
        results=agent_results,
        total_tokens=total_tokens,
        total_latency_ms=total_latency,
        timestamp=time.time(),
        summary=AnalysisSummary(
            successful_agents=successful_agents,
            failed_agents=failed_agents,
            recommendations=recommendations,
        ),
    )


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
        # Use LangGraph workflow for analysis
        result = await run_analysis(symbol, market.value, "en")

        # Dispatch embedding for RAG search (best-effort)
        try:
            from worker.tasks.embedding_tasks import embed_analysis_report
            for agent_type in ["fundamental", "technical", "sentiment", "news"]:
                agent_result = result.get(agent_type)
                if agent_result and agent_result.success:
                    content = getattr(agent_result, 'raw_content', None) or getattr(agent_result, 'content', None)
                    if content:
                        embed_analysis_report.delay({
                            "source_id": f"analysis-{symbol}-{agent_type}-{int(time.time())}",
                            "symbol": symbol,
                            "agent_type": agent_type,
                            "content": content,
                        })
        except Exception as embed_err:
            logger.warning("Failed to dispatch analysis embedding: %s", embed_err)

        # Transform LangGraph result to legacy response format
        return _transform_langgraph_result_to_response(result, symbol, market.value)

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
            # Start heartbeat task
            heartbeat_task = asyncio.create_task(heartbeat_sender())

            # Emit analysis_phase_start wrapper
            yield f'data: {json.dumps({"type": "analysis_phase_start", "timestamp": time.time()})}\n\n'
            last_event_time = time.time()

            try:
                async with asyncio.timeout(STREAMING_TIMEOUT_SECONDS):
                    # Use LangGraph stream_analysis
                    async for event in stream_analysis(symbol, market.value, lang):
                        # Check for client disconnect
                        if await request.is_disconnected():
                            logger.info(f"Client disconnected during streaming for {symbol}")
                            break

                        # Check for overall timeout
                        if time.time() - start_time > STREAMING_TIMEOUT_SECONDS:
                            logger.warning(f"Streaming timeout reached for {symbol}")
                            yield f'data: {json.dumps({"type": "timeout", "message": "Analysis timeout reached", "timestamp": time.time()})}\n\n'
                            break

                        # Format event as SSE
                        event_type = event.get("type", "unknown")
                        event_data = event.get("data", {})
                        sse_event = {
                            "type": event_type,
                            "timestamp": time.time(),
                            **event_data,
                        }
                        yield f'data: {json.dumps(sse_event)}\n\n'
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
        # Use LangGraph single agent execution
        result = await run_single_agent("fundamental", symbol, market.value, "en")
        agent_result = result.get("fundamental")

        if not agent_result:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Fundamental analysis failed to produce results.",
            )

        return SingleAnalysisResponse(
            symbol=symbol,
            market=market.value,
            agent_type=AgentTypeEnum.FUNDAMENTAL,
            success=agent_result.success,
            content=getattr(agent_result, 'raw_content', None) or getattr(agent_result, 'content', None),
            structured_data=getattr(agent_result, 'structured_data', None),
            error=agent_result.error if not agent_result.success else None,
            tokens_used=getattr(agent_result, 'tokens_used', 0) or 0,
            latency_ms=getattr(agent_result, 'latency_ms', 0) or 0,
            timestamp=time.time(),
        )

    except HTTPException:
        raise
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
        # Use LangGraph single agent execution
        result = await run_single_agent("technical", symbol, market.value, "en")
        agent_result = result.get("technical")

        if not agent_result:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Technical analysis failed to produce results.",
            )

        return SingleAnalysisResponse(
            symbol=symbol,
            market=market.value,
            agent_type=AgentTypeEnum.TECHNICAL,
            success=agent_result.success,
            content=getattr(agent_result, 'raw_content', None) or getattr(agent_result, 'content', None),
            structured_data=getattr(agent_result, 'structured_data', None),
            error=agent_result.error if not agent_result.success else None,
            tokens_used=getattr(agent_result, 'tokens_used', 0) or 0,
            latency_ms=getattr(agent_result, 'latency_ms', 0) or 0,
            timestamp=time.time(),
        )

    except HTTPException:
        raise
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
        # Use LangGraph single agent execution
        result = await run_single_agent("sentiment", symbol, market.value, "en")
        agent_result = result.get("sentiment")

        if not agent_result:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Sentiment analysis failed to produce results.",
            )

        return SingleAnalysisResponse(
            symbol=symbol,
            market=market.value,
            agent_type=AgentTypeEnum.SENTIMENT,
            success=agent_result.success,
            content=getattr(agent_result, 'raw_content', None) or getattr(agent_result, 'content', None),
            structured_data=getattr(agent_result, 'structured_data', None),
            error=agent_result.error if not agent_result.success else None,
            tokens_used=getattr(agent_result, 'tokens_used', 0) or 0,
            latency_ms=getattr(agent_result, 'latency_ms', 0) or 0,
            timestamp=time.time(),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Sentiment analysis error for {symbol}: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Analysis service temporarily unavailable. Please try again later.",
        )


@router.get(
    "/status",
    summary="Analysis service status",
    description="Get the status of the analysis service including available agents and workflow info.",
)
async def get_analysis_status(
    current_user: User = Depends(get_current_user),
):
    """
    Get analysis service status.

    Returns:
    - Workflow status (available, agents, capabilities)
    - Available analysis agents
    """
    try:
        from app.agents.langgraph import get_workflow_info

        workflow_info = get_workflow_info()
        return {
            "status": "healthy",
            "workflow": "langgraph",
            "agents": workflow_info.get("parallel_nodes", []),
            "supports_streaming": workflow_info.get("supports_streaming", True),
            "max_clarification_rounds": workflow_info.get("max_clarification_rounds", 2),
        }
    except Exception as e:
        logger.exception(f"Status check error: {e}")
        return {"status": "degraded", "error": "Service status check failed"}


# ============== LangGraph-based Endpoints ==============


@router.get(
    "/{symbol}/stream/v2",
    responses={
        400: {"model": AnalysisErrorResponse, "description": "Invalid symbol"},
        429: {"model": AnalysisErrorResponse, "description": "Rate limit exceeded"},
    },
    summary="LangGraph streaming analysis",
    description="Get streaming AI analysis using LangGraph workflow with Server-Sent Events (SSE).",
)
async def get_langgraph_streaming_analysis(
    request: Request,
    symbol: str,
    language: str = Query("en", description="Language for analysis output (en or zh)"),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(AI_ANALYSIS_RATE_LIMIT),
    _ai_config: None = Depends(apply_user_ai_config),
):
    """
    Get streaming AI analysis using LangGraph workflow.

    This endpoint uses the new layered LLM architecture with LangGraph:
    - Analysis layer: 4 parallel agents (fundamental, technical, sentiment, news)
    - Synthesis layer: Combines results and can request clarifications
    - Supports up to 2 clarification rounds for better accuracy

    Returns a stream of SSE events:
    - `start`: Analysis workflow started
    - `analysis_phase_start`: Analysis agents starting
    - `agent_start`: Individual agent started
    - `agent_complete`: Agent finished with results
    - `analysis_phase_complete`: All agents finished
    - `synthesis_phase_start`: Synthesis layer starting
    - `synthesis_chunk`: Streaming synthesis output
    - `clarification_needed`: Clarification requested (optional)
    - `clarification_start`: Clarification in progress (optional)
    - `clarification_complete`: Clarification done (optional)
    - `synthesis_complete`: Final synthesis done
    - `complete`: Workflow completed
    - `error`: Error occurred
    - `heartbeat`: Keep-alive signal

    **Rate Limit**: 10 requests per minute per user
    """
    symbol = validate_symbol(symbol)
    market = detect_market(symbol)
    lang = "zh" if language.lower().startswith("zh") else "en"

    logger.info(
        f"LangGraph streaming analysis requested for {symbol} by user {current_user.id} (lang={lang})"
    )

    async def event_generator():
        """Generate SSE events from LangGraph workflow."""
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
                        if time.time() - last_event_time >= HEARTBEAT_INTERVAL_SECONDS:
                            await heartbeat_queue.put(
                                f'data: {json.dumps({"type": "heartbeat", "timestamp": time.time()})}\n\n'
                            )
            except asyncio.CancelledError:
                pass

        try:
            from app.agents.langgraph import stream_analysis

            # Start heartbeat task
            heartbeat_task = asyncio.create_task(heartbeat_sender())

            # Emit analysis_phase_start wrapper
            yield f'data: {json.dumps({"type": "analysis_phase_start", "timestamp": time.time()})}\n\n'
            last_event_time = time.time()

            try:
                async with asyncio.timeout(STREAMING_TIMEOUT_SECONDS):
                    async for event in stream_analysis(symbol, market.value, lang):
                        # Check for client disconnect
                        if await request.is_disconnected():
                            logger.info(f"Client disconnected during LangGraph streaming for {symbol}")
                            break

                        # Check for overall timeout
                        if time.time() - start_time > STREAMING_TIMEOUT_SECONDS:
                            logger.warning(f"LangGraph streaming timeout for {symbol}")
                            yield f'data: {json.dumps({"type": "timeout", "message": "Analysis timeout reached", "timestamp": time.time()})}\n\n'
                            break

                        # Format event as SSE
                        event_type = event.get("type", "unknown")
                        event_data = event.get("data", {})

                        sse_event = {
                            "type": event_type,
                            "timestamp": time.time(),
                            **event_data,
                        }

                        yield f'data: {json.dumps(sse_event)}\n\n'
                        last_event_time = time.time()

                        # Yield any pending heartbeat events
                        while not heartbeat_queue.empty():
                            try:
                                hb_event = heartbeat_queue.get_nowait()
                                yield hb_event
                                last_event_time = time.time()
                            except asyncio.QueueEmpty:
                                break

            except asyncio.TimeoutError:
                logger.warning(f"LangGraph streaming timeout for {symbol}")
                yield f'data: {json.dumps({"type": "timeout", "message": "Analysis timeout reached", "timestamp": time.time()})}\n\n'

            # Note: analysis_phase_complete is now emitted by LangGraph workflow
            # when collect_results completes, before synthesis starts

        except ImportError as e:
            logger.error(f"LangGraph not available: {e}")
            yield f'data: {json.dumps({"type": "error", "error": "LangGraph workflow not available. Please check server configuration.", "timestamp": time.time()})}\n\n'

        except Exception as e:
            logger.exception(f"LangGraph streaming error for {symbol}: {e}")
            yield f'data: {json.dumps({"type": "error", "error": "Analysis service error. Please try again later.", "timestamp": time.time()})}\n\n'

        finally:
            analysis_complete = True
            if heartbeat_task and not heartbeat_task.done():
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
            logger.debug(f"LangGraph streaming cleanup completed for {symbol}")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/{symbol}/langgraph",
    summary="LangGraph full analysis",
    description="Get full AI analysis using LangGraph workflow (non-streaming).",
)
async def get_langgraph_analysis(
    symbol: str,
    language: str = Query("en", description="Language for analysis output (en or zh)"),
    current_user: User = Depends(get_current_user),
    _rate_limit: None = Depends(AI_ANALYSIS_RATE_LIMIT),
    _ai_config: None = Depends(apply_user_ai_config),
):
    """
    Get full AI analysis using LangGraph workflow.

    This endpoint uses the new layered LLM architecture with LangGraph.
    Unlike the streaming endpoint, this returns the complete analysis
    once all agents and synthesis are finished.

    **Rate Limit**: 10 requests per minute per user

    **Note**: This endpoint may take 30-60 seconds as it runs the complete
    LangGraph workflow including potential clarification rounds.
    """
    symbol = validate_symbol(symbol)
    market = detect_market(symbol)
    lang = "zh" if language.lower().startswith("zh") else "en"

    logger.info(
        f"LangGraph full analysis requested for {symbol} by user {current_user.id} (lang={lang})"
    )

    try:
        from app.agents.langgraph import run_analysis

        result = await run_analysis(symbol, market.value, lang)

        # Extract agent results
        agent_results = {}
        for agent_type in ["fundamental", "technical", "sentiment", "news"]:
            agent_result = result.get(agent_type)
            if agent_result:
                agent_results[agent_type] = {
                    "success": agent_result.success,
                    "content": agent_result.content if agent_result.success else None,
                    "structured_data": agent_result.structured_data if hasattr(agent_result, 'structured_data') else None,
                    "error": agent_result.error if not agent_result.success else None,
                    "confidence": agent_result.confidence if hasattr(agent_result, 'confidence') else None,
                    "tokens_used": agent_result.tokens_used if hasattr(agent_result, 'tokens_used') else None,
                    "latency_ms": agent_result.latency_ms if hasattr(agent_result, 'latency_ms') else None,
                }

        return {
            "symbol": symbol,
            "market": market.value,
            "language": lang,
            "agents": agent_results,
            "synthesis": result.get("synthesis_output", ""),
            "clarification_rounds": result.get("clarification_round", 0),
            "errors": result.get("errors", []),
            "timestamp": time.time(),
        }

    except ImportError as e:
        logger.error(f"LangGraph not available: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LangGraph workflow not available. Please check server configuration.",
        )

    except Exception as e:
        logger.exception(f"LangGraph analysis error for {symbol}: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Analysis service temporarily unavailable. Please try again later.",
        )


@router.get(
    "/langgraph/info",
    summary="LangGraph workflow info",
    description="Get information about the LangGraph workflow configuration.",
)
async def get_langgraph_info(
    current_user: User = Depends(get_current_user),
):
    """
    Get information about the LangGraph workflow.

    Returns configuration details about the layered LLM architecture
    including model settings and clarification parameters.
    """
    try:
        from app.agents.langgraph import get_workflow_info
        from app.core.llm_config import get_langgraph_settings, get_model_info

        workflow_info = get_workflow_info()
        model_info = get_model_info()

        # Try to get database settings
        try:
            langgraph_settings = await get_langgraph_settings()
        except Exception as e:
            logger.warning(f"Could not get LangGraph settings from database: {e}")
            langgraph_settings = {
                "max_clarification_rounds": 2,
                "clarification_confidence_threshold": 0.6,
                "use_local_models": False,
                "analysis_model": "gpt-4o-mini",
                "synthesis_model": "gpt-4o",
            }

        return {
            "status": "available",
            "workflow": workflow_info,
            "models": model_info,
            "settings": langgraph_settings,
        }

    except ImportError as e:
        logger.warning(f"LangGraph not available: {e}")
        return {
            "status": "unavailable",
            "error": "LangGraph dependencies not installed",
        }
