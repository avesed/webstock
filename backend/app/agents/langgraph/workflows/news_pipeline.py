"""LangGraph workflow for per-article news processing pipeline.

Content is pre-fetched by Layer 1.5 (batch_fetch_content) and saved to file storage.
This Layer 2 pipeline reads the file, filters, and embeds the article.

Workflow graph:
    START -> read_file -> route_filter_mode
                           |-- two_phase: deep_filter -> route_decision
                           +-- legacy: single_filter -> route_decision
                                                         |-- keep: embed -> update_db -> END
                                                         +-- delete: mark_deleted -> update_db -> END
"""

import logging
import time
import uuid

from langgraph.graph import END, StateGraph

from app.agents.langgraph.state import NewsProcessingState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Node functions
# ---------------------------------------------------------------------------


async def read_file_node(state: NewsProcessingState) -> dict:
    """Read article content from file storage.

    Layer 1.5 (batch_fetch_content) has already fetched the content and saved
    it to a JSON file. This node reads that file and populates the state with
    the full text and metadata for downstream LLM processing.
    """
    from app.services.pipeline_trace_service import PipelineTraceService

    t0 = time.monotonic()

    file_path = state.get("file_path")
    if not file_path:
        logger.warning("read_file_node: no file_path in state for news_id=%s", state["news_id"])
        elapsed = (time.monotonic() - t0) * 1000
        return {
            "final_status": "failed",
            "error": "No file_path provided — content not fetched by Layer 1.5",
            "trace_events": [PipelineTraceService.make_event(
                news_id=state["news_id"], layer="2", node="read_file",
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
                news_id=state["news_id"], layer="2", node="read_file",
                status="error", duration_ms=elapsed,
                error=f"Cannot read content from {file_path}",
            )],
        }

    logger.info(
        "read_file_node: loaded %d chars from %s for news_id=%s",
        len(content_data["full_text"]), file_path, state["news_id"],
    )

    elapsed = (time.monotonic() - t0) * 1000
    return {
        "full_text": content_data["full_text"],
        "word_count": content_data.get("word_count", 0),
        "language": content_data.get("language"),
        "authors": content_data.get("authors"),
        "keywords": content_data.get("keywords"),
        "trace_events": [PipelineTraceService.make_event(
            news_id=state["news_id"], layer="2", node="read_file",
            status="success", duration_ms=elapsed,
            metadata={"word_count": content_data.get("word_count", 0), "language": content_data.get("language")},
        )],
    }


def route_filter_mode(state: NewsProcessingState) -> str:
    """Route to filter mode based on state.

    Returns:
        "two_phase" -- use deep filter with entity extraction
        "legacy"    -- use single-stage relevance filter
        "end"       -- skip filtering (fetch failed)
    """
    if state.get("final_status") == "failed":
        return "end"
    if state.get("use_two_phase"):
        return "two_phase"
    return "legacy"


async def deep_filter_node(state: NewsProcessingState) -> dict:
    """Run deep (phase 2) LLM filter on full article text."""
    from worker.db_utils import get_task_session
    from app.skills.registry import get_skill_registry
    from app.services.filter_stats_service import get_filter_stats_service
    from app.services.pipeline_trace_service import PipelineTraceService

    t0 = time.monotonic()

    registry = get_skill_registry()
    skill = registry.get("deep_filter_news")
    if skill is None:
        logger.warning("deep_filter_news skill not found in registry")
        elapsed = (time.monotonic() - t0) * 1000
        return {
            "filter_decision": "keep",
            "trace_events": [PipelineTraceService.make_event(
                news_id=state["news_id"], layer="2", node="deep_filter",
                status="error", duration_ms=elapsed,
                error="skill not found",
            )],
        }

    stats_service = get_filter_stats_service()

    full_text = state.get("full_text") or state.get("summary") or ""
    title = state.get("title", "")
    source = state.get("source", "")  # Passed from Layer 1.5 via Celery params
    url = state["url"]

    # Execute deep filter skill (needs db for LLM config resolution)
    async with get_task_session() as db:
        result = await skill.safe_execute(
            timeout=30.0,
            title=title,
            full_text=full_text,
            source=source,
            url=url,
            db=db,
        )

    if not result.success:
        logger.warning(
            "deep_filter_node failed for %s: %s", state["news_id"], result.error
        )
        elapsed = (time.monotonic() - t0) * 1000
        # On filter failure, default to keep (don't lose articles)
        return {
            "filter_decision": "keep",
            "trace_events": [PipelineTraceService.make_event(
                news_id=state["news_id"], layer="2", node="deep_filter",
                status="error", duration_ms=elapsed,
                error=str(result.error)[:200] if result.error else "unknown error",
            )],
        }

    data = result.data
    decision = data.get("decision", "keep")

    logger.info(
        "deep_filter_node: news_id=%s, decision=%s", state["news_id"], decision,
    )

    # Track stats
    if decision == "delete":
        await stats_service.increment("fine_delete")
    else:
        await stats_service.increment("fine_keep")

    elapsed = (time.monotonic() - t0) * 1000
    return {
        "filter_decision": decision,
        "entities": data.get("entities"),
        "industry_tags": data.get("industry_tags"),
        "event_tags": data.get("event_tags"),
        "sentiment_tag": data.get("sentiment", "neutral"),
        "investment_summary": data.get("investment_summary", ""),
        "trace_events": [PipelineTraceService.make_event(
            news_id=state["news_id"], layer="2", node="deep_filter",
            status="success", duration_ms=elapsed,
            metadata={"decision": decision, "entity_count": len(data.get("entities") or []), "sentiment_tag": data.get("sentiment", "neutral")},
        )],
    }


async def single_filter_node(state: NewsProcessingState) -> dict:
    """Run legacy single-stage LLM relevance filter."""
    from worker.db_utils import get_task_session
    from app.services.news_filter_service import get_news_filter_service
    from app.services.two_phase_filter_service import get_news_llm_settings
    from app.services.pipeline_trace_service import PipelineTraceService

    t0 = time.monotonic()

    async with get_task_session() as db:
        llm_settings = await get_news_llm_settings(db)

    filter_service = get_news_filter_service(model=llm_settings.model)

    try:
        should_keep = await filter_service.evaluate_relevance(
            title=state.get("title", ""),
            summary=state.get("summary", ""),
            full_text=state.get("full_text"),
            source="",  # Not critical for legacy filter
            symbol=state.get("symbol"),
            model=llm_settings.model,
            system_api_key=llm_settings.api_key,
            system_base_url=llm_settings.base_url,
        )
    except Exception as e:
        logger.warning("single_filter_node failed for %s: %s", state["news_id"], e)
        elapsed = (time.monotonic() - t0) * 1000
        return {
            "filter_decision": "keep",
            "trace_events": [PipelineTraceService.make_event(
                news_id=state["news_id"], layer="2", node="single_filter",
                status="error", duration_ms=elapsed,
                error=str(e)[:200],
            )],
        }  # Default to keep on error

    decision = "keep" if should_keep else "delete"
    logger.info(
        "single_filter_node: news_id=%s, decision=%s", state["news_id"], decision,
    )
    elapsed = (time.monotonic() - t0) * 1000
    return {
        "filter_decision": decision,
        "trace_events": [PipelineTraceService.make_event(
            news_id=state["news_id"], layer="2", node="single_filter",
            status="success", duration_ms=elapsed,
            metadata={"decision": decision},
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
    from worker.db_utils import get_task_session
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
                news_id=state["news_id"], layer="2", node="embed",
                status="error", duration_ms=elapsed,
                error="embed_document skill not found in registry",
            )],
        }
    stats_service = get_filter_stats_service()

    # Build content for embedding
    content_parts = []
    if state.get("title"):
        content_parts.append(state["title"])
    if state.get("full_text"):
        content_parts.append(state["full_text"])
    elif state.get("summary"):
        content_parts.append(state["summary"])
    content = "\n\n".join(content_parts)

    if not content.strip():
        logger.warning("embed_node: no content to embed for news_id=%s", state["news_id"])
        elapsed = (time.monotonic() - t0) * 1000
        return {
            "final_status": "failed",
            "error": "No content to embed",
            "trace_events": [PipelineTraceService.make_event(
                news_id=state["news_id"], layer="2", node="embed",
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
                news_id=state["news_id"], layer="2", node="embed",
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
            news_id=state["news_id"], layer="2", node="embed",
            status="success", duration_ms=elapsed,
            metadata={"chunks_total": data.get("chunks_total", 0), "chunks_stored": data.get("chunks_stored", 0)},
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
            news_id=state["news_id"], layer="2", node="mark_deleted",
            status="success", duration_ms=elapsed,
            metadata={"file_path": file_path},
        )],
    }


async def update_db_node(state: NewsProcessingState) -> dict:
    """Update the News database record with final pipeline results."""
    from worker.db_utils import get_task_session
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

                # Update deep filter metadata if available
                if state.get("entities") is not None:
                    entities = state["entities"]
                    news.related_entities = entities if entities else None
                    if entities:
                        news.has_stock_entities = any(
                            e["type"] == "stock" for e in entities
                        )
                        news.has_macro_entities = any(
                            e["type"] == "macro" for e in entities
                        )
                        news.max_entity_score = max(
                            (e["score"] for e in entities), default=None
                        )
                        stock_entities = [e for e in entities if e["type"] == "stock"]
                        if stock_entities:
                            news.primary_entity = stock_entities[0]["entity"]
                            news.primary_entity_type = "stock"
                        elif entities:
                            news.primary_entity = entities[0]["entity"]
                            news.primary_entity_type = entities[0]["type"]

                if state.get("industry_tags"):
                    news.industry_tags = state["industry_tags"]
                if state.get("event_tags"):
                    news.event_tags = state["event_tags"]
                if state.get("sentiment_tag"):
                    news.sentiment_tag = state["sentiment_tag"]
                if state.get("investment_summary"):
                    news.investment_summary = state["investment_summary"]

                if state.get("use_two_phase"):
                    news.filter_status = FilterStatus.FINE_KEEP.value

            elif final_status == "deleted":
                news.content_status = ContentStatus.DELETED.value
                news.content_file_path = None
                if state.get("use_two_phase"):
                    news.filter_status = FilterStatus.FINE_DELETE.value

            elif final_status == "failed":
                error = state.get("error", "")
                if news.content_status not in (
                    ContentStatus.FAILED.value,
                    ContentStatus.BLOCKED.value,
                ):
                    # Only update if not already set by Layer 1.5
                    if "embed" in error.lower():
                        news.content_status = ContentStatus.EMBEDDING_FAILED.value
                    # else: keep whatever Layer 1.5 set

            await db.commit()
            logger.info(
                "update_db_node: news_id=%s, final_status=%s", news_id, final_status
            )

            # Write pipeline trace events
            elapsed = (time.monotonic() - t0) * 1000
            try:
                from app.services.pipeline_trace_service import PipelineTraceService
                all_events = list(state.get("trace_events", []))
                all_events.append(PipelineTraceService.make_event(
                    news_id=news_id, layer="2", node="update_db",
                    status="success", duration_ms=elapsed,
                    metadata={"final_status": final_status,
                               "content_status": news.content_status if news else None},
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
                    news_id=news_id, layer="2", node="update_db",
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
    """Create the news processing pipeline workflow graph."""
    workflow = StateGraph(NewsProcessingState)

    # Add nodes
    workflow.add_node("read_file", read_file_node)
    workflow.add_node("deep_filter", deep_filter_node)
    workflow.add_node("single_filter", single_filter_node)
    workflow.add_node("embed", embed_node)
    workflow.add_node("mark_deleted", mark_deleted_node)
    workflow.add_node("update_db", update_db_node)

    # START -> read_file
    workflow.add_edge("__start__", "read_file")

    # read_file -> route by filter mode
    workflow.add_conditional_edges(
        "read_file",
        route_filter_mode,
        {
            "two_phase": "deep_filter",
            "legacy": "single_filter",
            "end": "update_db",  # Skip filter if read failed
        },
    )

    # deep_filter / single_filter -> route by decision
    workflow.add_conditional_edges(
        "deep_filter",
        route_decision,
        {
            "keep": "embed",
            "delete": "mark_deleted",
        },
    )

    workflow.add_conditional_edges(
        "single_filter",
        route_decision,
        {
            "keep": "embed",
            "delete": "mark_deleted",
        },
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
    use_two_phase: bool = False,
    source: str = "",
    file_path: str = None,
) -> NewsProcessingState:
    """Run the news processing pipeline for a single article.

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
        use_two_phase=use_two_phase,
        source=source,
        file_path=file_path,
    )

    logger.info("Starting news pipeline for news_id=%s, url=%s", news_id, url[:80])

    final_state = await pipeline.ainvoke(initial_state)

    logger.info(
        "News pipeline completed for news_id=%s: status=%s",
        news_id,
        final_state.get("final_status", "unknown"),
    )

    return final_state
