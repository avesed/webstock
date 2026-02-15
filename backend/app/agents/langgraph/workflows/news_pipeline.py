"""LangGraph workflow for per-article news processing pipeline (Layer 3).

Content is pre-fetched by Layer 2 (batch_fetch_content) and saved to file storage.
Scoring is done in Layer 1 (monitor tasks) and content cleaning in Layer 2.
This Layer 3 pipeline reads the file, routes by processing_path, runs analysis
or lightweight extraction, embeds, and updates the DB.

Workflow graph:
    START -> read_file -> route_by_processing_path
                           |-- full_analysis: multi_agent_analysis -> route_decision
                           |-- lightweight:   lightweight_filter   -> route_decision
                           +-- end:           update_db (skip on read failure)
                                               route_decision:
                                                 |-- keep:   embed -> update_db -> END
                                                 +-- delete: mark_deleted -> update_db -> END
"""

import logging
import time
import uuid
from typing import Optional

from langgraph.graph import END, StateGraph

from app.agents.langgraph.state import NewsProcessingState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Node functions
# ---------------------------------------------------------------------------


async def read_file_node(state: NewsProcessingState) -> dict:
    """Read article content from file storage.

    Layer 2 (batch_fetch_content) has already fetched the content, cleaned it,
    and saved it to a JSON file. This node reads that file and populates the
    state with the full text and metadata for downstream LLM processing.
    """
    from app.services.pipeline_trace_service import PipelineTraceService

    t0 = time.monotonic()

    file_path = state.get("file_path")
    if not file_path:
        logger.warning("read_file_node: no file_path in state for news_id=%s", state["news_id"])
        elapsed = (time.monotonic() - t0) * 1000
        return {
            "final_status": "failed",
            "error": "No file_path provided — content not fetched by Layer 2",
            "trace_events": [PipelineTraceService.make_event(
                news_id=state["news_id"], layer="3", node="read_file",
                status="error", duration_ms=elapsed,
                error="No file_path provided",
            )],
        }

    from app.services.news_storage_service import get_news_storage_service

    storage = get_news_storage_service()
    content_data = storage.read_content(file_path)

    if not content_data or not content_data.get("full_text"):
        logger.warning(
            "read_file_node: cannot read content from %s for news_id=%s",
            file_path, state["news_id"],
        )
        elapsed = (time.monotonic() - t0) * 1000
        return {
            "final_status": "failed",
            "error": f"Cannot read content from {file_path}",
            "trace_events": [PipelineTraceService.make_event(
                news_id=state["news_id"], layer="3", node="read_file",
                status="error", duration_ms=elapsed,
                error=f"Cannot read content from {file_path}",
            )],
        }

    # Prefer cleaned_text (from Layer 2 content cleaning) over raw full_text
    full_text = content_data.get("cleaned_text") or content_data.get("full_text", "")

    # Load image insights (pre-extracted in Layer 2 content cleaning)
    image_insights = content_data.get("image_insights") or None
    has_visual_data = content_data.get("has_visual_data", False)

    logger.info(
        "read_file_node: loaded %d chars from %s for news_id=%s "
        "(has_visual_data=%s, image_insights=%d chars)",
        len(full_text), file_path, state["news_id"],
        has_visual_data, len(image_insights) if image_insights else 0,
    )

    elapsed = (time.monotonic() - t0) * 1000
    return {
        "full_text": full_text,
        "word_count": content_data.get("word_count", 0),
        "language": content_data.get("language"),
        "authors": content_data.get("authors"),
        "keywords": content_data.get("keywords"),
        "image_insights": image_insights,
        "has_visual_data": has_visual_data,
        "trace_events": [PipelineTraceService.make_event(
            news_id=state["news_id"], layer="3", node="read_file",
            status="success", duration_ms=elapsed,
            metadata={
                "word_count": content_data.get("word_count", 0),
                "language": content_data.get("language"),
                "has_visual_data": has_visual_data,
                "has_image_insights": bool(image_insights),
            },
        )],
    }


def route_by_processing_path(state: NewsProcessingState) -> str:
    """Route based on processing_path set by Layer 1 scoring.

    The processing_path is determined by Layer 1 scoring in the monitor tasks
    and passed through via Celery task args. No LLM call needed.

    Returns:
        "full_analysis" -- high-score articles for 5-agent deep analysis
        "lightweight"   -- low-score articles for quick extraction
        "end"           -- skip processing (read failed or no path set)
    """
    if state.get("final_status") == "failed":
        return "end"
    path = state.get("processing_path")
    if path == "full_analysis":
        return "full_analysis"
    if path == "lightweight":
        return "lightweight"
    # Default: lightweight for unknown paths
    return "lightweight"


async def multi_agent_analysis_node(state: NewsProcessingState) -> dict:
    """Run 5-agent parallel deep analysis (full_analysis path)."""
    from app.db.task_session import get_task_session
    from app.services.multi_agent_filter_service import get_multi_agent_filter_service
    from app.services.pipeline_trace_service import PipelineTraceService

    t0 = time.monotonic()
    service = get_multi_agent_filter_service()

    full_text = state.get("full_text") or state.get("summary") or ""
    title = state.get("title", "")
    image_insights = state.get("image_insights") or ""
    symbol = state.get("symbol", "")

    try:
        async with get_task_session() as db:
            result = await service.full_analysis(
                db=db,
                title=title,
                cleaned_text=full_text,
                image_insights=image_insights,
                symbol=symbol,
            )

        elapsed = (time.monotonic() - t0) * 1000
        logger.info(
            "multi_agent_analysis_node: news_id=%s, decision=%s, "
            "entities=%d, cache_hit=%.1f%% (%.0fms)",
            state["news_id"], result.decision,
            len(result.entities),
            result.cache_stats.get("cache_hit_rate", 0) * 100,
            elapsed,
        )

        return {
            "filter_decision": result.decision,
            "entities": result.entities,
            "industry_tags": result.industry_tags,
            "event_tags": result.event_tags,
            "sentiment_tag": result.sentiment,
            "investment_summary": result.investment_summary,
            "detailed_summary": result.detailed_summary,
            "analysis_report": result.analysis_report,
            "cache_metadata": result.cache_stats,
            "trace_events": [PipelineTraceService.make_event(
                news_id=state["news_id"], layer="3", node="multi_agent_analysis",
                status="success", duration_ms=elapsed,
                metadata={
                    "decision": result.decision,
                    "entity_count": len(result.entities),
                    "sentiment": result.sentiment,
                    "cache_hit_rate": result.cache_stats.get("cache_hit_rate", 0),
                    "detailed_summary_length": len(result.detailed_summary),
                    "analysis_report_length": len(result.analysis_report),
                },
                cache_metadata=result.cache_stats,
            )],
        }

    except Exception as e:
        elapsed = (time.monotonic() - t0) * 1000
        logger.error(
            "multi_agent_analysis_node failed for %s: %s (%.0fms)",
            state["news_id"], e, elapsed,
        )
        # Fail-open: keep article with empty metadata
        return {
            "filter_decision": "keep",
            "trace_events": [PipelineTraceService.make_event(
                news_id=state["news_id"], layer="3", node="multi_agent_analysis",
                status="error", duration_ms=elapsed,
                error=str(e)[:200],
            )],
        }


async def lightweight_filter_node(state: NewsProcessingState) -> dict:
    """Quick entity/tag extraction for low-score articles (lightweight path)."""
    from app.db.task_session import get_task_session
    from app.services.lightweight_filter_service import get_lightweight_filter_service
    from app.services.pipeline_trace_service import PipelineTraceService

    t0 = time.monotonic()
    service = get_lightweight_filter_service()

    full_text = state.get("full_text") or state.get("summary") or ""
    title = state.get("title", "")
    url = state["url"]

    try:
        async with get_task_session() as db:
            result = await service.process_article(
                db=db,
                title=title,
                text=full_text,
                url=url,
            )

        elapsed = (time.monotonic() - t0) * 1000
        logger.info(
            "lightweight_filter_node: news_id=%s, decision=%s, entities=%d (%.0fms)",
            state["news_id"], result.decision, len(result.entities), elapsed,
        )

        lw_metadata: dict = {
            "decision": result.decision,
            "entity_count": len(result.entities),
            "sentiment": result.sentiment,
        }
        if result.raw_response:
            lw_metadata["raw_response"] = result.raw_response

        return {
            "filter_decision": result.decision,
            "entities": result.entities,
            "industry_tags": result.industry_tags,
            "event_tags": result.event_tags,
            "sentiment_tag": result.sentiment,
            "investment_summary": result.investment_summary,
            "detailed_summary": result.detailed_summary,
            "analysis_report": result.analysis_report,
            "trace_events": [PipelineTraceService.make_event(
                news_id=state["news_id"], layer="3", node="lightweight_filter",
                status="success", duration_ms=elapsed,
                metadata=lw_metadata,
            )],
        }

    except Exception as e:
        elapsed = (time.monotonic() - t0) * 1000
        logger.error(
            "lightweight_filter_node failed for %s: %s (%.0fms)",
            state["news_id"], e, elapsed,
        )
        return {
            "filter_decision": "keep",
            "trace_events": [PipelineTraceService.make_event(
                news_id=state["news_id"], layer="3", node="lightweight_filter",
                status="error", duration_ms=elapsed,
                error=str(e)[:200],
            )],
        }


def route_decision(state: NewsProcessingState) -> str:
    """Route based on filter decision.

    Returns:
        "keep"   -- article is relevant, proceed to embedding
        "delete" -- article should be removed
    """
    decision = state.get("filter_decision", "keep")
    if decision == "delete":
        return "delete"
    return "keep"


async def embed_node(state: NewsProcessingState) -> dict:
    """Generate embeddings for the article content."""
    from app.db.task_session import get_task_session
    from app.skills.registry import get_skill_registry
    from app.services.filter_stats_service import get_filter_stats_service
    from app.services.pipeline_trace_service import PipelineTraceService

    t0 = time.monotonic()

    registry = get_skill_registry()
    skill = registry.get("embed_document")
    if skill is None:
        logger.warning("embed_node: embed_document skill not found in registry")
        elapsed = (time.monotonic() - t0) * 1000
        return {
            "final_status": "failed",
            "error": "embed_document skill not found in registry",
            "trace_events": [PipelineTraceService.make_event(
                news_id=state["news_id"], layer="3", node="embed",
                status="error", duration_ms=elapsed,
                error="embed_document skill not found in registry",
            )],
        }
    stats_service = get_filter_stats_service()

    # Build content for embedding
    # Prefer detailed_summary (LLM-refined, higher signal-to-noise) over raw full_text
    if state.get("detailed_summary"):
        embed_strategy = "detailed_summary"
        content_parts = []
        if state.get("title"):
            content_parts.append(state["title"])
        if state.get("summary"):
            content_parts.append(state["summary"])
        content_parts.append(state["detailed_summary"])
        content = "\n\n".join(content_parts)
    else:
        embed_strategy = "full_text_fallback"
        content_parts = []
        if state.get("title"):
            content_parts.append(state["title"])
        if state.get("full_text"):
            content_parts.append(state["full_text"])
        elif state.get("summary"):
            content_parts.append(state["summary"])
        content = "\n\n".join(content_parts)

    logger.info(
        "embed_node: news_id=%s, strategy=%s, content_length=%d",
        state["news_id"], embed_strategy, len(content),
    )

    if not content.strip():
        logger.warning("embed_node: no content to embed for news_id=%s", state["news_id"])
        elapsed = (time.monotonic() - t0) * 1000
        return {
            "final_status": "failed",
            "error": "No content to embed",
            "trace_events": [PipelineTraceService.make_event(
                news_id=state["news_id"], layer="3", node="embed",
                status="error", duration_ms=elapsed,
                error="No content to embed",
            )],
        }

    async with get_task_session() as db:
        result = await skill.safe_execute(
            timeout=120.0,
            source_type="news",
            source_id=state["news_id"],
            content=content,
            symbol=state.get("symbol"),
            db=db,
        )

    if not result.success:
        logger.warning("embed_node failed for %s: %s", state["news_id"], result.error)
        await stats_service.increment("embedding_error")
        elapsed = (time.monotonic() - t0) * 1000
        return {
            "final_status": "failed",
            "error": result.error,
            "trace_events": [PipelineTraceService.make_event(
                news_id=state["news_id"], layer="3", node="embed",
                status="error", duration_ms=elapsed,
                error=str(result.error)[:200] if result.error else "unknown error",
            )],
        }

    data = result.data or {}
    await stats_service.increment("embedding_success")

    elapsed = (time.monotonic() - t0) * 1000
    return {
        "chunks_total": data.get("chunks_total", 0),
        "chunks_stored": data.get("chunks_stored", 0),
        "final_status": "embedded",
        "trace_events": [PipelineTraceService.make_event(
            news_id=state["news_id"], layer="3", node="embed",
            status="success", duration_ms=elapsed,
            metadata={
                "chunks_total": data.get("chunks_total", 0),
                "chunks_stored": data.get("chunks_stored", 0),
                "embed_strategy": embed_strategy,
            },
        )],
    }


async def mark_deleted_node(state: NewsProcessingState) -> dict:
    """Mark the article as deleted and clean up content file."""
    from app.services.news_storage_service import get_news_storage_service
    from app.services.pipeline_trace_service import PipelineTraceService

    t0 = time.monotonic()

    file_path = state.get("file_path")
    if file_path:
        try:
            storage_service = get_news_storage_service()
            storage_service.delete_content(file_path)
            logger.info("mark_deleted_node: deleted content file %s for news_id=%s", file_path, state["news_id"])
        except Exception as e:
            logger.warning("Failed to delete content file %s: %s", file_path, e)

    elapsed = (time.monotonic() - t0) * 1000
    return {
        "final_status": "deleted",
        "file_path": None,
        "trace_events": [PipelineTraceService.make_event(
            news_id=state["news_id"], layer="3", node="mark_deleted",
            status="success", duration_ms=elapsed,
            metadata={"file_path": file_path},
        )],
    }


async def update_db_node(state: NewsProcessingState) -> dict:
    """Update the News database record with final pipeline results."""
    from app.db.task_session import get_task_session
    from sqlalchemy import select
    from app.models.news import News, ContentStatus, FilterStatus

    t0 = time.monotonic()
    news_id = state["news_id"]
    final_status = state.get("final_status", "pending")

    try:
        async with get_task_session() as db:
            query = select(News).where(News.id == uuid.UUID(news_id))
            res = await db.execute(query)
            news = res.scalar_one_or_none()
            if not news:
                logger.warning("update_db_node: news record not found: %s", news_id)
                return {}

            if final_status == "embedded":
                news.content_status = ContentStatus.EMBEDDED.value

                # Scoring fields (from Layer 1, passed via Celery task args)
                if state.get("content_score") is not None:
                    news.content_score = state["content_score"]
                if state.get("processing_path") is not None:
                    news.processing_path = state["processing_path"]
                if state.get("score_details") is not None:
                    news.score_details = state["score_details"]
                if state.get("image_insights") is not None:
                    news.image_insights = state["image_insights"]
                if "has_visual_data" in state:
                    news.has_visual_data = state["has_visual_data"]

                # Update filter metadata if available
                if state.get("entities") is not None:
                    entities = state["entities"]
                    news.related_entities = entities if entities else None
                    if entities:
                        news.has_stock_entities = any(
                            e.get("type") == "stock" for e in entities
                        )
                        news.has_macro_entities = any(
                            e.get("type") == "macro" for e in entities
                        )
                        news.max_entity_score = max(
                            (e.get("score", 0.5) for e in entities), default=None
                        )
                        stock_entities = [e for e in entities if e.get("type") == "stock"]
                        if stock_entities:
                            news.primary_entity = stock_entities[0].get("entity", "")
                            news.primary_entity_type = "stock"
                        elif entities:
                            news.primary_entity = entities[0].get("entity", "")
                            news.primary_entity_type = entities[0].get("type", "stock")

                if state.get("industry_tags"):
                    news.industry_tags = state["industry_tags"]
                if state.get("event_tags"):
                    news.event_tags = state["event_tags"]
                if state.get("sentiment_tag"):
                    news.sentiment_tag = state["sentiment_tag"]
                if state.get("investment_summary"):
                    news.investment_summary = state["investment_summary"]

                # Save detailed_summary
                if state.get("detailed_summary"):
                    if hasattr(news, "detailed_summary"):
                        expected_summary = state["detailed_summary"]
                        news.detailed_summary = expected_summary
                        await db.flush()  # Flush to detect ORM/constraint errors

                        # Verify field was actually saved
                        if news.detailed_summary is None:
                            logger.error(
                                "update_db_node: detailed_summary is NULL after save for news_id=%s "
                                "(expected %d chars — possible ORM type mismatch or constraint violation)",
                                news_id, len(expected_summary),
                            )
                        elif len(news.detailed_summary) != len(expected_summary):
                            logger.error(
                                "update_db_node: detailed_summary length mismatch for news_id=%s: "
                                "expected %d chars, got %d chars (possible truncation)",
                                news_id, len(expected_summary), len(news.detailed_summary),
                            )
                        else:
                            logger.info(
                                "update_db_node: saved detailed_summary for news_id=%s: %d chars",
                                news_id, len(expected_summary),
                            )
                    else:
                        logger.debug(
                            "update_db_node: detailed_summary column not yet available "
                            "(pending migration), skipping for news_id=%s", news_id,
                        )

                # Save analysis_report to existing ai_analysis column
                if state.get("analysis_report"):
                    expected_analysis = state["analysis_report"]
                    news.ai_analysis = expected_analysis
                    await db.flush()  # Flush to detect ORM/constraint errors

                    # Verify field was actually saved
                    if news.ai_analysis is None:
                        logger.error(
                            "update_db_node: ai_analysis is NULL after save for news_id=%s "
                            "(expected %d chars — possible ORM type mismatch or constraint violation)",
                            news_id, len(expected_analysis),
                        )
                    elif len(news.ai_analysis) != len(expected_analysis):
                        logger.error(
                            "update_db_node: ai_analysis length mismatch for news_id=%s: "
                            "expected %d chars, got %d chars (possible truncation)",
                            news_id, len(expected_analysis), len(news.ai_analysis),
                        )
                    else:
                        logger.info(
                            "update_db_node: saved ai_analysis for news_id=%s: %d chars",
                            news_id, len(expected_analysis),
                        )

                # Always set filter_status for pipeline-processed articles
                news.filter_status = FilterStatus.FINE_KEEP.value

            elif final_status == "deleted":
                news.content_status = ContentStatus.DELETED.value
                news.content_file_path = None
                # Always set filter_status for pipeline-processed articles
                news.filter_status = FilterStatus.FINE_DELETE.value

                # Persist scoring metadata even for deleted articles
                if state.get("content_score") is not None:
                    news.content_score = state["content_score"]
                if state.get("processing_path") is not None:
                    news.processing_path = state["processing_path"]
                if state.get("score_details") is not None:
                    news.score_details = state["score_details"]
                if state.get("image_insights") is not None:
                    news.image_insights = state["image_insights"]
                if "has_visual_data" in state:
                    news.has_visual_data = state["has_visual_data"]

            elif final_status == "failed":
                error = state.get("error", "")
                if news.content_status not in (
                    ContentStatus.FAILED.value,
                    ContentStatus.BLOCKED.value,
                ):
                    # Only update if not already set by Layer 2
                    if "embed" in error.lower():
                        news.content_status = ContentStatus.EMBEDDING_FAILED.value
                    # else: keep whatever Layer 2 set

            await db.commit()
            logger.info(
                "update_db_node: news_id=%s, final_status=%s", news_id, final_status
            )

            # Write pipeline trace events
            elapsed = (time.monotonic() - t0) * 1000
            try:
                from app.services.pipeline_trace_service import PipelineTraceService

                # Build trace metadata with content generation stats
                trace_metadata = {
                    "final_status": final_status,
                    "content_status": news.content_status if news else None,
                    "detailed_summary_length": len(state.get("detailed_summary") or ""),
                    "analysis_report_length": len(state.get("analysis_report") or ""),
                    "has_detailed_content": bool(state.get("detailed_summary")),
                    "has_analysis_report": bool(state.get("analysis_report")),
                }
                # Trace enrichment with scoring metadata
                if state.get("content_score") is not None:
                    trace_metadata["content_score"] = state["content_score"]
                if state.get("processing_path"):
                    trace_metadata["processing_path"] = state["processing_path"]
                if state.get("cache_metadata"):
                    trace_metadata["cache_metadata"] = state["cache_metadata"]

                all_events = list(state.get("trace_events", []))
                all_events.append(PipelineTraceService.make_event(
                    news_id=news_id, layer="3", node="update_db",
                    status="success", duration_ms=elapsed,
                    metadata=trace_metadata,
                ))
                await PipelineTraceService.record_events_batch(db, all_events)
                await db.commit()
            except Exception as trace_err:
                logger.warning("update_db_node: trace write failed: %s", trace_err)

    except Exception as e:
        logger.error(
            "update_db_node: DB update failed for news_id=%s: %s", news_id, e,
        )
        # Try to record trace events even on DB failure
        elapsed = (time.monotonic() - t0) * 1000
        try:
            from app.services.pipeline_trace_service import PipelineTraceService
            async with get_task_session() as trace_db:
                all_events = list(state.get("trace_events", []))
                all_events.append(PipelineTraceService.make_event(
                    news_id=news_id, layer="3", node="update_db",
                    status="error", duration_ms=elapsed, error=str(e)[:200],
                ))
                await PipelineTraceService.record_events_batch(trace_db, all_events)
                await trace_db.commit()
        except Exception as trace_err:
            logger.debug("update_db_node: fallback trace write also failed: %s", trace_err)

    return {}


# ---------------------------------------------------------------------------
# Workflow graph construction
# ---------------------------------------------------------------------------


def create_news_pipeline() -> StateGraph:
    """Create the news processing pipeline workflow graph.

    Simplified graph (scoring done in Layer 1, cleaning in Layer 2):
        START -> read_file -> route_by_processing_path
          |-- "full_analysis" -> multi_agent_analysis -> route_decision
          |-- "lightweight"   -> lightweight_filter   -> route_decision
          +-- "end"           -> update_db (skip processing on read failure)
                                  route_decision:
                                    |-- "keep"   -> embed -> update_db -> END
                                    +-- "delete" -> mark_deleted -> update_db -> END
    """
    workflow = StateGraph(NewsProcessingState)

    # Add nodes
    workflow.add_node("read_file", read_file_node)
    workflow.add_node("multi_agent_analysis", multi_agent_analysis_node)
    workflow.add_node("lightweight_filter", lightweight_filter_node)
    workflow.add_node("embed", embed_node)
    workflow.add_node("mark_deleted", mark_deleted_node)
    workflow.add_node("update_db", update_db_node)

    # START -> read_file
    workflow.add_edge("__start__", "read_file")

    # read_file -> route by processing_path
    workflow.add_conditional_edges(
        "read_file",
        route_by_processing_path,
        {
            "full_analysis": "multi_agent_analysis",
            "lightweight": "lightweight_filter",
            "end": "update_db",
        },
    )

    # Analysis nodes -> route by decision
    workflow.add_conditional_edges(
        "multi_agent_analysis",
        route_decision,
        {"keep": "embed", "delete": "mark_deleted"},
    )

    workflow.add_conditional_edges(
        "lightweight_filter",
        route_decision,
        {"keep": "embed", "delete": "mark_deleted"},
    )

    # embed / mark_deleted -> update_db
    workflow.add_edge("embed", "update_db")
    workflow.add_edge("mark_deleted", "update_db")

    # update_db -> END
    workflow.add_edge("update_db", END)

    return workflow.compile()


# Module-level compiled workflow (singleton)
_compiled_pipeline = None


def get_news_pipeline():
    """Get the compiled news pipeline workflow (singleton)."""
    global _compiled_pipeline
    if _compiled_pipeline is None:
        _compiled_pipeline = create_news_pipeline()
    return _compiled_pipeline


async def run_news_pipeline(
    news_id: str,
    url: str,
    market: str = "US",
    symbol: str = "",
    title: str = "",
    summary: str = "",
    published_at: str = None,
    source: str = "",
    file_path: str = None,
    content_score: Optional[int] = None,
    processing_path: Optional[str] = None,
    score_details: Optional[dict] = None,
) -> NewsProcessingState:
    """Run the news processing pipeline for a single article.

    Args:
        content_score: 0-300 content score from Layer 1 scoring (3 agents x 0-100).
        processing_path: 'full_analysis' or 'lightweight' from Layer 1 scoring.
        score_details: Dimension scores, reasoning, critical flag from Layer 1.

    Returns the final state dict.
    """
    from app.agents.langgraph.state import create_news_processing_state

    pipeline = get_news_pipeline()
    initial_state = create_news_processing_state(
        news_id=news_id,
        url=url,
        market=market,
        symbol=symbol,
        title=title,
        summary=summary,
        published_at=published_at,
        source=source,
        file_path=file_path,
        content_score=content_score,
        processing_path=processing_path,
        score_details=score_details,
    )

    logger.info("Starting news pipeline for news_id=%s, url=%s", news_id, url[:80])

    final_state = await pipeline.ainvoke(initial_state)

    logger.info(
        "News pipeline completed for news_id=%s: status=%s",
        news_id,
        final_state.get("final_status", "unknown"),
    )

    return final_state
