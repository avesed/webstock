"""Layer 2 multi-agent deep analysis service.

Runs 5 specialized agents in parallel with shared prompt cache for
comprehensive article analysis. Each agent receives the same system
message + article context (with cache_control=ephemeral), then a
unique instruction message. Agent 1 writes the cache; Agents 2-5
read from cache (~90% cost saving on input tokens).

Used by the news pipeline Layer 2 for articles that pass the initial
filter with high scores.
"""

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm import get_llm_gateway, ChatRequest, Message, Role
from app.services.news_layer3_analysis_service import (
    extract_json_from_response,
    validate_entities,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_CONTENT_LENGTH = 20000  # Max chars of cleaned article text sent to LLM
AGENT_TIMEOUT = 120  # Seconds per agent LLM call

# ---------------------------------------------------------------------------
# Shared prompt (cached across all 5 agents)
# ---------------------------------------------------------------------------

BASE_ANALYSIS_SYSTEM = """你是专业的金融新闻分析团队的一员。你将分析以下新闻文章，根据你的专业角色提供结构化的分析结果。所有输出必须为JSON格式。

## 分析框架

### 基本面维度
评估新闻对公司基本面的影响：
- 营收与利润：财报数据、营收增长率、利润率变化、盈利预期调整
- 估值影响：市盈率(PE)、市净率(PB)、市销率(PS)的隐含变化
- 竞争格局：市场份额变化、新竞争者进入、行业整合
- 管理层变动：CEO/CFO更换、董事会重组、管理团队评价
- 资本结构：融资活动、股票回购、分红政策、债务水平变化

### 技术面维度
关注新闻可能触发的技术信号：
- 价格影响：支撑位/阻力位突破、缺口、趋势线变化
- 成交量：放量上涨/下跌、缩量整理、异常成交
- 动量指标：RSI超买超卖、MACD金叉死叉、均线系统变化
- 波动率：VIX变化、隐含波动率、历史波动率对比

### 情绪维度
评估市场情绪和投资者心理：
- 市场情绪指标：恐惧贪婪指数、看涨看跌比率、融资融券数据
- 投资者行为：资金流向、持仓变化、大宗交易
- 媒体影响：报道基调、传播范围、意见领袖观点
- 社交媒体：散户讨论热度、情绪极端值、共识偏离

### 宏观维度
分析宏观环境和政策影响：
- 货币政策：利率决议、央行表态、流动性变化
- 财政政策：税收政策、政府支出、产业补贴
- 国际关系：贸易摩擦、制裁政策、地缘政治
- 经济数据：GDP、CPI、PMI、就业数据、零售数据

## 实体识别标准

### 股票代码格式
- 美股：1-5位大写字母（如 AAPL、MSFT、GOOGL、TSLA、NVDA）
- A股：6位数字+交易所后缀（如 600519.SH 上交所、000858.SZ 深交所）
- 港股：4-5位数字+.HK（如 0700.HK、9988.HK、1810.HK）
- 指数：标准代码（如 SPX、IXIC、DJI、000001.SH、HSI、HSCEI）

### 实体分类
- stock：个股，必须使用标准股票代码
- index：指数，使用标准指数代码
- macro：宏观因素，使用简短中英文名称（如"Fed利率"、"CPI"、"美元指数"、"原油价格"）

### 实体评分标准（score: 0.0-1.0）
- 0.9-1.0：新闻直接讨论该实体，是核心主题
- 0.7-0.89：新闻显著提及该实体，有实质性关联
- 0.5-0.69：新闻间接关联，可能受到影响
- 0.3-0.49：弱关联，仅在行业/板块层面
- 0.0-0.29：边缘关联，可忽略

## 行业分类体系
tech(科技/互联网/半导体/软件)、finance(银行/保险/券商/金融科技)、healthcare(医药/医疗器械/生物科技)、energy(石油/天然气/新能源/电力)、consumer(零售/食品/奢侈品/家电)、industrial(制造/航空/国防/机械)、materials(化工/钢铁/有色金属/建材)、utilities(公用事业/水务/燃气)、realestate(房地产/REITs)、telecom(电信/通信设备)

## 事件分类体系
earnings(财报/业绩预告/盈利警告)、merger(并购/重组/分拆/私有化)、ipo(IPO/增发/配股/退市)、regulatory(监管/合规/反垄断/处罚)、executive(高管变动/董事会/股权激励)、product(新产品/技术突破/专利)、lawsuit(诉讼/知识产权/集体诉讼)、dividend(分红/派息/特别股息)、buyback(回购/注销/库存股)、guidance(业绩指引/展望/预测调整)、macro(宏观政策/央行/经济数据)

## 输出质量要求
1. 数据准确：所有引用的数字、日期、公司名必须与原文一致
2. 逻辑清晰：因果关系明确，不做无依据的推断
3. 投资导向：每个分析结论都应指向可操作的投资建议
4. 中立客观：区分事实与观点，标明不确定性
5. 格式规范：严格遵循JSON格式要求，字段名和值类型必须正确"""

# ---------------------------------------------------------------------------
# Per-agent instruction prompts
# ---------------------------------------------------------------------------

ENTITY_EXTRACTION_PROMPT = """你的角色：实体提取专家
提取所有关联的股票、指数和宏观因素实体。

输出JSON：
{
  "entities": [
    {"entity": "AAPL", "type": "stock", "score": 0.95},
    {"entity": "Fed利率", "type": "macro", "score": 0.7}
  ]
}

注意：
- type=stock的entity必须使用股票代码（如AAPL, 600519.SH, 0700.HK），不要用公司名
- type=index: 指数代码（如SPX, IXIC, 000001.SH, HSI）
- type=macro: 宏观因素，用简短中文/英文名（如Fed利率、CPI、美元指数）
- 最多6个实体，score范围0.0-1.0"""

SENTIMENT_TAGS_PROMPT = """你的角色：情绪与标签分析师
判断新闻情绪和分类标签。

输出JSON：
{
  "sentiment": "bullish/bearish/neutral",
  "industry_tags": ["tech", "semiconductor"],
  "event_tags": ["earnings", "guidance"]
}

industry_tags选项: tech/finance/healthcare/energy/consumer/industrial/materials/utilities/realestate/telecom
event_tags选项: earnings/merger/ipo/regulatory/executive/product/lawsuit/dividend/buyback/guidance/macro
- 每类最多5个标签"""

SUMMARY_GENERATION_PROMPT = """你的角色：摘要生成专家
生成投资导向的摘要内容。

输出JSON：
{
  "investment_summary": "1句话概况（不超过50字）",
  "detailed_summary": "保留所有关键细节的完整总结，包含重要数据、时间线、人物、因果关系"
}

要求：
- investment_summary: 精炼的1句话，不超过50字，用于卡片预览
- detailed_summary: 保留所有关键信息，长度5-20句话，视复杂程度调整。删除冗余表述，但不能遗漏重要数据和因果关系"""

IMPACT_ASSESSMENT_PROMPT = """你的角色：影响力评估师
评估新闻对市场、行业和个股的影响。

输出JSON：
{
  "market_impact": "对整体市场的影响分析",
  "sector_impact": "对相关行业板块的影响",
  "stock_impact": "对具体个股的影响分析",
  "time_horizon": "short_term/medium_term/long_term",
  "impact_magnitude": "high/medium/low"
}

要求：
- 每个影响字段2-3句话，数据和结论要有理有据
- time_horizon: 影响的主要时间维度
- impact_magnitude: 综合影响强度"""

REPORT_WRITING_PROMPT = """你的角色：报告撰写专家
撰写Markdown格式的专业分析报告。

**重要：analysis_report的值必须是一个Markdown字符串，不能是嵌套JSON对象。**
用\\n表示换行，将整个报告放在一个字符串值中。

输出JSON：
{
  "analysis_report": "## 核心解读\\n用通俗易懂的语言解释...\\n\\n## 投资洞察\\n- **机会点**：...\\n- **关注点**：...\\n- **时间窗口**：...\\n\\n## 风险分析\\n- **短期风险**：...\\n- **长期风险**：...\\n- **不确定性**：...\\n\\n## 市场影响\\n- **直接影响板块**：...\\n- **间接影响**：...\\n\\n## 情绪指数\\n**综合情绪**：看涨/中性/看跌\\n**情绪强度**：X/5\\n**理由**：...\\n\\n## 专业信息\\n- **相关公司**：...\\n- **关键数据**：...\\n- **时间线**：..."
}

报告必须包含6个章节（核心解读、投资洞察、风险分析、市场影响、情绪指数、专业信息）。
每章节2-4句话，数据和结论要有理有据。"""

# Agent name → instruction prompt mapping
AGENT_PROMPTS: Dict[str, str] = {
    "entity_extractor": ENTITY_EXTRACTION_PROMPT,
    "sentiment_tags": SENTIMENT_TAGS_PROMPT,
    "summary_generator": SUMMARY_GENERATION_PROMPT,
    "impact_assessor": IMPACT_ASSESSMENT_PROMPT,
    "report_writer": REPORT_WRITING_PROMPT,
}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class MultiAgentResult:
    """Combined result from 5-agent analysis."""

    decision: str  # always "keep" for articles that reach this service
    entities: List[Dict[str, Any]]
    sentiment: str
    industry_tags: List[str]
    event_tags: List[str]
    investment_summary: str
    detailed_summary: str
    analysis_report: str
    market_context: Optional[Dict[str, Any]]
    cache_stats: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Agent result container (internal)
# ---------------------------------------------------------------------------

@dataclass
class _AgentResponse:
    """Internal container for a single agent's parsed output + usage stats."""

    agent_name: str
    data: Dict[str, Any]
    raw_content: str = ""  # Raw LLM output for debugging
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    elapsed_ms: float = 0.0
    success: bool = True
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class MultiAgentFilterService:
    """5-Agent parallel analysis with shared prompt cache.

    All 5 agents share the same system message + article context which
    is marked with ``cache_control={"type": "ephemeral"}`` so that the
    first agent populates the prompt cache and subsequent agents read
    from it (Anthropic / OpenAI prompt caching).
    """

    async def full_analysis(
        self,
        db: AsyncSession,
        title: str,
        cleaned_text: str,
        image_insights: str,
        symbol: str,
    ) -> MultiAgentResult:
        """Run 5 agents in parallel with shared prompt cache.

        Args:
            db: Database session for resolving model config.
            title: Article title.
            cleaned_text: Cleaned article full text (will be truncated
                to ``MAX_CONTENT_LENGTH``).
            image_insights: Image analysis results (may be empty).
            symbol: Stock symbol associated with the article.

        Returns:
            MultiAgentResult with combined analysis from all agents.
        """
        t0 = time.monotonic()

        # ------------------------------------------------------------------
        # 1. Resolve model config
        # ------------------------------------------------------------------
        from app.services.settings_service import get_settings_service

        settings_service = get_settings_service()

        try:
            model_config = await settings_service.resolve_model_provider(
                db, "phase2_layer2_analysis"
            )
        except ValueError as e:
            logger.error(
                "MultiAgentFilterService: cannot resolve model config: %s", e
            )
            return self._empty_result(error_reason=str(e))

        if not model_config.api_key:
            logger.error(
                "MultiAgentFilterService: no API key for phase2_layer2_analysis purpose"
            )
            return self._empty_result(
                error_reason="No API key configured for news_filter"
            )

        # ------------------------------------------------------------------
        # 2. Build shared base messages (with cache_control)
        # ------------------------------------------------------------------
        truncated_text = cleaned_text[:MAX_CONTENT_LENGTH]

        article_context_parts = [f"标题: {title}"]
        if symbol:
            article_context_parts.append(f"关联股票: {symbol}")
        if image_insights:
            article_context_parts.append(f"图片信息: {image_insights}")
        article_context_parts.append(f"\n全文:\n{truncated_text}")

        article_context = "\n".join(article_context_parts)

        # Shared messages: system + article context
        # Both carry cache_control so the prompt prefix is cached.
        base_messages = [
            Message(
                role=Role.SYSTEM,
                content=BASE_ANALYSIS_SYSTEM,
                cache_control={"type": "ephemeral"},
            ),
            Message(
                role=Role.USER,
                content=article_context,
                cache_control={"type": "ephemeral"},
            ),
        ]

        # ------------------------------------------------------------------
        # 3. Run 5 agents in parallel
        # ------------------------------------------------------------------
        tasks = [
            self._run_agent(
                agent_name=name,
                base_messages=base_messages,
                instruction=prompt,
                model_config=model_config,
            )
            for name, prompt in AGENT_PROMPTS.items()
        ]

        logger.info(
            "MultiAgentFilterService: starting 5 agents for symbol=%s title=%s",
            symbol,
            title[:80],
        )

        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        # ------------------------------------------------------------------
        # 4. Collect agent responses, handle failures
        # ------------------------------------------------------------------
        agent_responses: Dict[str, _AgentResponse] = {}
        for name, result in zip(AGENT_PROMPTS.keys(), raw_results):
            if isinstance(result, Exception):
                logger.warning(
                    "Agent '%s' raised exception: %s", name, result
                )
                agent_responses[name] = _AgentResponse(
                    agent_name=name,
                    data={},
                    success=False,
                    error=str(result),
                )
            else:
                agent_responses[name] = result

        # Log per-agent status
        succeeded = sum(1 for r in agent_responses.values() if r.success)
        failed = len(agent_responses) - succeeded
        logger.info(
            "MultiAgentFilterService: %d/%d agents succeeded for %s",
            succeeded,
            len(agent_responses),
            title[:60],
        )
        if failed:
            failed_names = [
                n for n, r in agent_responses.items() if not r.success
            ]
            logger.warning(
                "Failed agents: %s", ", ".join(failed_names)
            )

        # ------------------------------------------------------------------
        # 5. Merge results
        # ------------------------------------------------------------------
        merged = self._merge_agent_results(agent_responses)

        # ------------------------------------------------------------------
        # 6. Compute cache statistics
        # ------------------------------------------------------------------
        total_prompt = sum(r.prompt_tokens for r in agent_responses.values())
        total_completion = sum(
            r.completion_tokens for r in agent_responses.values()
        )
        total_cached = sum(r.cached_tokens for r in agent_responses.values())
        total_tokens = total_prompt + total_completion

        cache_hit_rate = (
            (total_cached / total_prompt) if total_prompt > 0 else 0.0
        )

        cache_stats = {
            "total_tokens": total_tokens,
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
            "cached_tokens": total_cached,
            "cache_hit_rate": round(cache_hit_rate, 4),
            "agents_succeeded": succeeded,
            "agents_failed": failed,
            "elapsed_ms": round((time.monotonic() - t0) * 1000, 1),
            "per_agent": {
                name: {
                    "success": resp.success,
                    "elapsed_ms": round(resp.elapsed_ms, 1),
                    "prompt_tokens": resp.prompt_tokens,
                    "cached_tokens": resp.cached_tokens,
                    "completion_tokens": resp.completion_tokens,
                    "raw_output": resp.raw_content,
                }
                for name, resp in agent_responses.items()
            },
        }

        # Track token usage for stats dashboard (aggregate + per-agent)
        if total_prompt > 0 or total_completion > 0:
            try:
                from app.services.filter_stats_service import (
                    get_filter_stats_service,
                )

                stats_service = get_filter_stats_service()
                await stats_service.track_tokens(
                    stage="deep_multi_agent",
                    input_tokens=total_prompt,
                    output_tokens=total_completion,
                )
                # Per-agent token tracking
                for name, resp in agent_responses.items():
                    if resp.success and (resp.prompt_tokens > 0 or resp.completion_tokens > 0):
                        await stats_service.track_tokens(
                            stage=f"agent_{name}",
                            input_tokens=resp.prompt_tokens,
                            output_tokens=resp.completion_tokens,
                        )
            except Exception as e:
                logger.debug(
                    "Failed to track multi-agent token stats: %s", e
                )

        elapsed_total = (time.monotonic() - t0) * 1000
        logger.info(
            "MultiAgentFilterService complete: symbol=%s, "
            "tokens=%d (cached=%d, hit_rate=%.1f%%), elapsed=%.0fms",
            symbol,
            total_tokens,
            total_cached,
            cache_hit_rate * 100,
            elapsed_total,
        )

        merged.cache_stats = cache_stats
        return merged

    async def _run_agent(
        self,
        agent_name: str,
        base_messages: List[Message],
        instruction: str,
        model_config: Any,
    ) -> _AgentResponse:
        """Run a single agent with shared prompt cache.

        Args:
            agent_name: Identifier for this agent (for logging/stats).
            base_messages: Shared system + article context messages
                (with cache_control already set).
            instruction: Agent-specific instruction prompt.
            model_config: ResolvedModelConfig from settings_service.

        Returns:
            _AgentResponse with parsed JSON data and token usage.
        """
        t0 = time.monotonic()

        # Build messages: shared base + agent-specific instruction
        messages = list(base_messages) + [
            Message(role=Role.USER, content=instruction),
        ]

        gateway = get_llm_gateway()

        chat_request = ChatRequest(
            model=model_config.model,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.3,
            timeout=AGENT_TIMEOUT,
        )

        try:
            response = await gateway.chat(
                chat_request,
                system_api_key=model_config.api_key,
                system_base_url=model_config.base_url,
                use_user_config=False,
                purpose="layer3_analysis",
                usage_metadata={"agent": agent_name},
            )

            elapsed_ms = (time.monotonic() - t0) * 1000

            # Extract token usage
            prompt_tokens = 0
            completion_tokens = 0
            cached_tokens = 0
            if response.usage:
                prompt_tokens = response.usage.prompt_tokens
                completion_tokens = response.usage.completion_tokens
                cached_tokens = response.usage.cached_tokens

            # Parse JSON from response
            content = response.content or ""
            data: dict = {}
            try:
                data = extract_json_from_response(content)
            except (ValueError, Exception) as json_err:
                logger.warning(
                    "Agent '%s' JSON extraction failed: %s (%d chars)",
                    agent_name, json_err, len(content),
                )

            if not data:
                # Store raw content as fallback (useful for report_writer
                # where the LLM may return raw markdown instead of JSON)
                if content.strip():
                    data = {"_raw_content": content.strip()}
                    logger.info(
                        "Agent '%s': stored raw content as fallback (%d chars)",
                        agent_name, len(content),
                    )

            logger.debug(
                "Agent '%s' completed: %d prompt tokens "
                "(cached=%d), %d completion tokens, %.0fms, "
                "keys=%s",
                agent_name,
                prompt_tokens,
                cached_tokens,
                completion_tokens,
                elapsed_ms,
                list(data.keys()) if data else "empty",
            )

            return _AgentResponse(
                agent_name=agent_name,
                data=data,
                raw_content=content[:5000],  # Retain for debugging (7d via pipeline_events)
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cached_tokens=cached_tokens,
                elapsed_ms=elapsed_ms,
                success=bool(data),
            )

        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.error(
                "Agent '%s' failed after %.0fms: %s",
                agent_name,
                elapsed_ms,
                e,
            )
            return _AgentResponse(
                agent_name=agent_name,
                data={},
                elapsed_ms=elapsed_ms,
                success=False,
                error=str(e),
            )

    def _merge_agent_results(
        self,
        agent_responses: Dict[str, _AgentResponse],
    ) -> MultiAgentResult:
        """Merge outputs from all 5 agents into a single result.

        Uses default values for any agent that failed or returned
        incomplete data.

        Args:
            agent_responses: Map of agent_name to _AgentResponse.

        Returns:
            MultiAgentResult with merged data.
        """
        # --- Entity Extractor ---
        entity_data = agent_responses.get("entity_extractor")
        raw_entities: List[Any] = []
        if entity_data and entity_data.success:
            raw_entities = entity_data.data.get("entities", [])
        entities = validate_entities(raw_entities)

        # --- Sentiment & Tags ---
        sentiment_data = agent_responses.get("sentiment_tags")
        sentiment = "neutral"
        industry_tags: List[str] = []
        event_tags: List[str] = []
        if sentiment_data and sentiment_data.success:
            raw_sentiment = sentiment_data.data.get("sentiment", "neutral")
            if raw_sentiment in ("bullish", "bearish", "neutral"):
                sentiment = raw_sentiment
            industry_tags = sentiment_data.data.get("industry_tags", [])[:5]
            event_tags = sentiment_data.data.get("event_tags", [])[:5]

            # Validate tag values
            valid_industry = {
                "tech", "finance", "healthcare", "energy", "consumer",
                "industrial", "materials", "utilities", "realestate", "telecom",
            }
            valid_events = {
                "earnings", "merger", "ipo", "regulatory", "executive",
                "product", "lawsuit", "dividend", "buyback", "guidance", "macro",
            }
            industry_tags = [
                t for t in industry_tags if t in valid_industry
            ]
            event_tags = [
                t for t in event_tags if t in valid_events
            ]

        # --- Summary Generator ---
        summary_data = agent_responses.get("summary_generator")
        investment_summary = ""
        detailed_summary = ""
        if summary_data and summary_data.success:
            investment_summary = (
                summary_data.data.get("investment_summary", "") or ""
            )[:500]
            detailed_summary = (
                summary_data.data.get("detailed_summary", "") or ""
            )

            # Validate minimum quality
            if len(investment_summary) < 2:
                logger.warning(
                    "investment_summary too short (%d chars), clearing",
                    len(investment_summary),
                )
                investment_summary = ""
            if len(detailed_summary) < 10:
                logger.warning(
                    "detailed_summary too short (%d chars), clearing",
                    len(detailed_summary),
                )
                detailed_summary = ""

        # --- Impact Assessor ---
        impact_data = agent_responses.get("impact_assessor")
        market_context: Optional[Dict[str, Any]] = None
        if impact_data and impact_data.success and impact_data.data:
            # Validate expected fields
            time_horizon = impact_data.data.get("time_horizon", "")
            if time_horizon not in (
                "short_term", "medium_term", "long_term"
            ):
                time_horizon = "medium_term"

            impact_magnitude = impact_data.data.get("impact_magnitude", "")
            if impact_magnitude not in ("high", "medium", "low"):
                impact_magnitude = "medium"

            market_context = {
                "market_impact": impact_data.data.get(
                    "market_impact", ""
                ),
                "sector_impact": impact_data.data.get(
                    "sector_impact", ""
                ),
                "stock_impact": impact_data.data.get(
                    "stock_impact", ""
                ),
                "time_horizon": time_horizon,
                "impact_magnitude": impact_magnitude,
            }

        # --- Report Writer ---
        report_data = agent_responses.get("report_writer")
        analysis_report = ""
        if report_data:
            raw_report = report_data.data.get("analysis_report", "")

            # LLMs sometimes return analysis_report as a structured JSON
            # object instead of a markdown string — convert to markdown.
            if isinstance(raw_report, dict) and raw_report:
                analysis_report = self._dict_to_markdown(raw_report)
                logger.info(
                    "Converted dict analysis_report to markdown (%d chars)",
                    len(analysis_report),
                )
            else:
                analysis_report = raw_report or ""

            # Fallback: if JSON parsing lost the report content,
            # use the raw LLM output which is likely raw markdown
            if not analysis_report:
                raw = report_data.data.get("_raw_content", "")
                if raw and len(raw) >= 50:
                    # Try to extract from raw: it may be JSON the extractor
                    # couldn't handle (e.g., literal newlines in strings)
                    # or direct markdown output
                    if raw.lstrip().startswith("{"):
                        # Attempt repair: replace literal newlines inside
                        # JSON strings with \n
                        try:
                            repaired = re.sub(
                                r'(?<=: ")(.*?)(?=")',
                                lambda m: m.group(0).replace("\n", "\\n"),
                                raw,
                                flags=re.DOTALL,
                            )
                            parsed = json.loads(repaired)
                            analysis_report = parsed.get(
                                "analysis_report", ""
                            ) or ""
                            if analysis_report:
                                logger.info(
                                    "Recovered analysis_report via JSON "
                                    "repair (%d chars)",
                                    len(analysis_report),
                                )
                        except Exception:
                            pass

                    # If still empty but raw looks like markdown, use directly
                    if not analysis_report and "##" in raw:
                        # Extract markdown starting from first ## header
                        md_start = raw.find("##")
                        analysis_report = raw[md_start:].strip()
                        logger.info(
                            "Using raw markdown as analysis_report "
                            "(%d chars, fallback)",
                            len(analysis_report),
                        )

            # Validate minimum quality
            if analysis_report and "##" not in analysis_report:
                logger.warning(
                    "analysis_report missing section headers, "
                    "report length=%d chars",
                    len(analysis_report),
                )
            if len(analysis_report) < 30:
                if analysis_report:
                    logger.warning(
                        "analysis_report too short (%d chars), clearing",
                        len(analysis_report),
                    )
                analysis_report = ""

        return MultiAgentResult(
            decision="keep",
            entities=entities,
            sentiment=sentiment,
            industry_tags=industry_tags,
            event_tags=event_tags,
            investment_summary=investment_summary,
            detailed_summary=detailed_summary,
            analysis_report=analysis_report,
            market_context=market_context,
        )

    @staticmethod
    def _empty_result(error_reason: str = "") -> MultiAgentResult:
        """Return a safe empty result when analysis cannot proceed.

        The decision defaults to "keep" (fail-open) to avoid dropping
        articles that may have investment value.

        Args:
            error_reason: Reason for the empty result (for logging).

        Returns:
            MultiAgentResult with empty/default values.
        """
        if error_reason:
            logger.warning(
                "Returning empty MultiAgentResult: %s", error_reason
            )
        return MultiAgentResult(
            decision="keep",
            entities=[],
            sentiment="neutral",
            industry_tags=[],
            event_tags=[],
            investment_summary="",
            detailed_summary="",
            analysis_report="",
            market_context=None,
            cache_stats={
                "total_tokens": 0,
                "cached_tokens": 0,
                "cache_hit_rate": 0.0,
                "error": error_reason,
            },
        )

    @staticmethod
    def _dict_to_markdown(d: Dict[str, Any], level: int = 2) -> str:
        """Convert a nested dict to markdown sections.

        Handles the case where the LLM returns ``analysis_report`` as a
        structured JSON object instead of a markdown string.

        Args:
            d: Dict with section headers as keys.
            level: Heading level (default 2 = ``##``).

        Returns:
            Markdown-formatted string.
        """
        parts: List[str] = []
        prefix = "#" * level

        for key, value in d.items():
            if isinstance(value, str):
                parts.append(f"{prefix} {key}\n{value}")
            elif isinstance(value, dict):
                # Sub-sections: render as bullet points
                lines = [f"{prefix} {key}"]
                for sub_key, sub_val in value.items():
                    if isinstance(sub_val, (list, tuple)):
                        lines.append(f"- **{sub_key}**：")
                        for item in sub_val:
                            if isinstance(item, dict):
                                # e.g. {"股票代码": "AAPL", "公司名称": "Apple"}
                                flat = "、".join(
                                    f"{k}: {v}" for k, v in item.items()
                                )
                                lines.append(f"  - {flat}")
                            else:
                                lines.append(f"  - {item}")
                    else:
                        lines.append(f"- **{sub_key}**：{sub_val}")
                parts.append("\n".join(lines))
            elif isinstance(value, (list, tuple)):
                lines = [f"{prefix} {key}"]
                for item in value:
                    if isinstance(item, dict):
                        flat = "、".join(
                            f"{k}: {v}" for k, v in item.items()
                        )
                        lines.append(f"- {flat}")
                    else:
                        lines.append(f"- {item}")
                parts.append("\n".join(lines))
            else:
                parts.append(f"{prefix} {key}\n{value}")

        return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_service: Optional[MultiAgentFilterService] = None


def get_multi_agent_filter_service() -> MultiAgentFilterService:
    """Get singleton instance of MultiAgentFilterService."""
    global _service
    if _service is None:
        _service = MultiAgentFilterService()
    return _service
