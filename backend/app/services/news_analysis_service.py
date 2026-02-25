"""On-demand news article deep analysis with token streaming.

Generates a 6-section Markdown analysis report for any news article,
streamed via SSE through TaskManager. Reports are cached in the
`news.ai_analysis` column after first generation.
"""

import logging
import time
from typing import Any, AsyncGenerator, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm import get_llm_gateway, ChatRequest, Message, Role
from app.models.news import News

logger = logging.getLogger(__name__)

# Reuse the system prompt from multi_agent_filter_service
ANALYSIS_SYSTEM_PROMPT = """你是专业的金融新闻深度分析师。你将对新闻文章进行全面深度分析，生成一份Markdown格式的专业投资分析报告。

## 分析框架

### 基本面维度
评估新闻对公司基本面的影响：营收与利润、估值影响、竞争格局、管理层变动、资本结构

### 技术面维度
关注新闻可能触发的技术信号：价格影响、成交量、动量指标、波动率

### 情绪维度
评估市场情绪和投资者心理：市场情绪指标、投资者行为、媒体影响

### 宏观维度
分析宏观环境和政策影响：货币政策、财政政策、国际关系、经济数据

## 输出质量要求
1. 数据准确：所有引用的数字、日期、公司名必须与原文一致
2. 逻辑清晰：因果关系明确，不做无依据的推断
3. 投资导向：每个分析结论都应指向可操作的投资建议
4. 中立客观：区分事实与观点，标明不确定性"""

REPORT_INSTRUCTION = """请基于以上新闻内容，撰写一份Markdown格式的专业分析报告。

报告必须包含以下6个章节：

## 核心解读
用通俗易懂的语言解释新闻的核心内容和背景，2-4句话。

## 投资洞察
- **机会点**：基于新闻内容的投资机会
- **关注点**：需要持续关注的要素
- **时间窗口**：影响的时间维度

## 风险分析
- **短期风险**：近期可能出现的风险
- **长期风险**：中长期需关注的风险
- **不确定性**：存在的不确定因素

## 市场影响
- **直接影响板块**：受直接影响的行业和个股
- **间接影响**：可能受到间接影响的领域

## 情绪指数
**综合情绪**：看涨/中性/看跌
**情绪强度**：X/5
**依据**：简要说明判断依据

## 专业信息
- **相关公司**：涉及的主要公司
- **关键数据**：新闻中的重要数据点
- **时间线**：关键事件时间节点

每章节2-4句话，数据和结论要有理有据。直接输出Markdown文本，不要包裹在代码块中。"""


class NewsAnalysisService:
    """Generate on-demand deep analysis reports for news articles."""

    async def stream_analysis(
        self,
        db: AsyncSession,
        news_id: str,
        force_new: bool = False,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Stream a deep analysis report for a news article.

        If the article already has a cached `ai_analysis`, yields it
        immediately as a single `complete` event (unless force_new=True).
        Otherwise, generates a new report via LLM streaming and saves it.

        Yields SSE-compatible dicts with `type` field.
        """
        import uuid as uuid_mod

        try:
            news_uuid = uuid_mod.UUID(news_id)
        except ValueError:
            yield {
                "type": "error",
                "data": {"message": f"Invalid news ID: {news_id}"},
                "timestamp": time.time(),
            }
            return

        # Load article
        result = await db.execute(select(News).where(News.id == news_uuid))
        article = result.scalar_one_or_none()
        if not article:
            yield {
                "type": "error",
                "data": {"message": "Article not found"},
                "timestamp": time.time(),
            }
            return

        # Check cache
        if article.ai_analysis and not force_new:
            logger.info(
                "新闻分析: 返回缓存报告 news_id=%s (%d chars)",
                news_id[:8], len(article.ai_analysis),
            )
            yield {
                "type": "analysis_start",
                "data": {"cached": True, "newsId": news_id},
                "timestamp": time.time(),
            }
            yield {
                "type": "complete",
                "data": {"report": article.ai_analysis},
                "timestamp": time.time(),
            }
            return

        # Build article content for analysis
        logger.info(
            "新闻分析: 缓存未命中, 开始生成 news_id=%s", news_id[:8],
        )
        content = self._build_article_content(article)
        if not content:
            yield {
                "type": "error",
                "data": {"message": "No content available for analysis"},
                "timestamp": time.time(),
            }
            return

        yield {
            "type": "analysis_start",
            "data": {"cached": False, "newsId": news_id},
            "timestamp": time.time(),
        }

        # Stream generation
        full_report = ""
        try:
            async for chunk in self._generate_report(db, article, content):
                full_report += chunk
                yield {
                    "type": "analysis_chunk",
                    "data": {"content": chunk},
                    "timestamp": time.time(),
                }

            # Save to DB
            if full_report and len(full_report) >= 30:
                try:
                    article.ai_analysis = full_report
                    await db.commit()
                    logger.info(
                        "新闻分析: 报告已保存 news_id=%s (%d chars)",
                        news_id[:8], len(full_report),
                    )
                except Exception as save_err:
                    logger.warning(
                        "新闻分析: 保存报告失败 news_id=%s: %s",
                        news_id[:8], save_err,
                    )

            yield {
                "type": "complete",
                "data": {"report": full_report},
                "timestamp": time.time(),
            }

        except Exception as e:
            logger.error(
                "新闻分析: 生成失败 news_id=%s: %s", news_id[:8], e,
            )
            # Save partial report if substantial content was generated
            if full_report and len(full_report) >= 100:
                try:
                    article.ai_analysis = full_report
                    await db.commit()
                    logger.info(
                        "新闻分析: 部分报告已保存 news_id=%s (%d chars)",
                        news_id[:8], len(full_report),
                    )
                except Exception:
                    pass  # Best-effort save
            yield {
                "type": "error",
                "data": {"message": f"Analysis generation failed: {str(e)[:200]}"},
                "timestamp": time.time(),
            }

    def _build_article_content(self, article: News) -> str:
        """Build article content string from available sources.

        Priority: content file (full text) > detailed_summary > summary
        """
        parts = [f"标题: {article.title}"]

        if article.symbol and article.symbol != "MARKET":
            parts.append(f"关联股票: {article.symbol}")

        # Try to load full text from content file
        full_text = None
        if article.content_file_path:
            try:
                from app.services.news_storage_service import get_news_storage_service
                storage = get_news_storage_service()
                content_data = storage.read_content(article.content_file_path)
                if content_data:
                    full_text = content_data.get("cleaned_text") or content_data.get("full_text")
            except Exception as e:
                logger.warning("Failed to load content file for %s: %s", str(article.id)[:8], e)

        if full_text:
            # Truncate to 20K chars (same as pipeline)
            parts.append(f"\n全文:\n{full_text[:20000]}")
        elif article.detailed_summary:
            parts.append(f"\n详细摘要:\n{article.detailed_summary}")
        elif article.summary:
            parts.append(f"\n摘要:\n{article.summary}")
        else:
            return ""

        # Add entity context if available
        if article.related_entities:
            entity_names = []
            for e in article.related_entities[:10]:
                name = e.get("company_name") or e.get("entity", "")
                if name:
                    entity_names.append(name)
            if entity_names:
                parts.append(f"\n相关实体: {', '.join(entity_names)}")

        return "\n".join(parts)

    async def _generate_report(
        self,
        db: AsyncSession,
        article: News,
        content: str,
    ) -> AsyncGenerator[str, None]:
        """Generate analysis report via LLM streaming."""
        from app.services.settings_service import get_settings_service

        settings_service = get_settings_service()

        # Resolve model using news_report purpose chain
        try:
            config = await settings_service.resolve_model_with_fallback(
                db, ["news_report", "phase2_layer2_analysis", "news_filter"]
            )
        except ValueError as e:
            raise RuntimeError(f"Cannot resolve model for news analysis: {e}")

        if not config.api_key:
            raise RuntimeError("No API key configured for news analysis")

        gateway = get_llm_gateway()

        messages = [
            Message(role=Role.SYSTEM, content=ANALYSIS_SYSTEM_PROMPT),
            Message(role=Role.USER, content=f"{content}\n\n---\n\n{REPORT_INSTRUCTION}"),
        ]

        chat_request = ChatRequest(
            model=config.model,
            messages=messages,
            temperature=0.4,
            timeout=120,
        )

        logger.info(
            "新闻分析: 开始流式生成 news_id=%s model=%s",
            str(article.id)[:8], config.model,
        )

        async for event in gateway.chat_stream(
            chat_request,
            system_api_key=config.api_key,
            system_base_url=config.base_url,
            use_user_config=False,
            purpose="news_report",
        ):
            from app.core.llm.types import ContentDelta
            if isinstance(event, ContentDelta) and event.text:
                yield event.text


# Singleton
_service: Optional[NewsAnalysisService] = None


def get_news_analysis_service() -> NewsAnalysisService:
    """Get singleton instance of NewsAnalysisService."""
    global _service
    if _service is None:
        _service = NewsAnalysisService()
    return _service
