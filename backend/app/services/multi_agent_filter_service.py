"""Layer 2 multi-agent deep analysis service.

Runs 2 specialized agents in parallel with shared prompt cache for
article analysis: entity_extractor (entities + themes) and
summary_sentiment (sentiment + tags + summaries).  Each agent receives
the same system message + article context (with cache_control=ephemeral),
then a unique instruction message.  Agent 1 writes the cache; Agent 2
reads from cache (~90% cost saving on input tokens).

Structured output uses ``response_format={"type":"json_schema"}``
(strict mode) instead of tool calling.  This provides:
  - Schema-enforced JSON output per agent (no parsing failures)
  - Full prompt cache sharing (response_format is NOT part of the
    cache key, so different schemas across agents still share cache)
  - No wrong-tool issues (no tools involved at all)

Used by the news pipeline Layer 3 for articles that pass scoring
with high content_score (full_analysis path).
"""

import asyncio
import json
import logging
import time
from collections import defaultdict
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
# Shared prompt (cached across all agents)
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
- A股：6位数字+交易所后缀（如 600519.SS 上交所、000858.SZ 深交所）
- 港股：4-5位数字+.HK（如 0700.HK、9988.HK、1810.HK）
- 指数：标准代码（如 SPX、IXIC、DJI、000001.SS、HSI、HSCEI）

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

ENTITY_EXTRACTION_PROMPT = """你的角色：实体提取专家（带联想能力）

## 任务
提取所有**直接提及**和**间接关联**的股票、指数、宏观因素实体。

## 联想维度（5个方向）
1. **行业同行**：同一细分行业的竞争对手和龙头
2. **供应链**：上游供应商、下游客户
3. **竞争者**：直接竞争关系
4. **受益方**：政策受益、技术溢出等间接受益
5. **子公司/母公司**：集团内关联

## 工作流程
1. 阅读新闻，识别核心主题和涉及的行业/概念
2. 判断新闻**主要关联的市场**（cn=A股/hk=港股/us=美股）
3. 提取直接提及的实体（公司、指数、宏观因素）
4. 识别需要联想扩展的行业主题（如"人形机器人"、"AI芯片"、"新能源汽车产业链"）
5. 在JSON中提交所有结果（entities + themes + primary_market）
   - 系统会**自动验证和修正**股票代码，你不需要自己查询
   - 系统会根据 themes **自动搜索**知识库找到关联股票
   - **primary_market** 告诉系统在哪个市场搜索关联股票
6. **如果新闻只涉及单一公司且无行业主题，themes 留空即可**

## 代码格式规则（尽量遵守，系统会自动修正）
| 市场 | 正确格式 | 常见错误 |
|------|----------|----------|
| A股 | 600519.SS, 000001.SZ | ~~600519~~, ~~SH600519~~, ~~000001~~ |
| 港股 | 0700.HK, 9988.HK | ~~HK0700~~, ~~01810.HK~~(应为1810.HK) |
| 美股 | AAPL, TSLA | ~~苹果~~(不可用中文名), ~~AAPL.US~~ |
| 贵金属 | GC=F, SI=F, PL=F, PA=F | ~~XAU~~, ~~XAUUSD~~, ~~GOLD~~ |
| 指数 | type=index: SPX, IXIC, 000001.SS, HSI | - |
| 宏观 | type=macro: Fed利率, CPI, 美元指数 | - |

**关键规则**：
- company_name是**必填字段**（stock类型），系统用它来自动校验和修正代码
- 不确定代码时，填写正确的company_name比猜测代码更重要

## 字段说明
- **entity**: 股票代码或指数/宏观因素名称
- **type**: stock / index / macro
- **company_name**: 公司中文或英文名（stock类型必填）
- **relation**: direct / industry_peer / supply_chain / competitor / beneficiary / subsidiary
- **score**: 0.8-1.0 直接提及 | 0.5-0.7 行业/供应链 | 0.3-0.5 间接关联

## primary_market 判断规则
- 中国国内政策、A股公司、人民币相关 → **cn**
- 港股公司、港股政策 → **hk**
- 美股公司、美国政策、美元相关 → **us**
- 涉及多个市场时，选**最主要**的那个市场

## 限制
- 最多15个实体，优先保留高相关度"""

SUMMARY_SENTIMENT_PROMPT = """你的角色：摘要与情绪分析师
综合分析新闻情绪并生成投资导向的摘要内容。

情绪判断：
- sentiment: bullish/bearish/neutral

标签分类：
- industry_tags选项: tech/finance/healthcare/energy/consumer/industrial/materials/utilities/realestate/telecom
- event_tags选项: earnings/merger/ipo/regulatory/executive/product/lawsuit/dividend/buyback/guidance/macro
- 每类最多5个标签

摘要要求：
- investment_summary: 精炼的1句话，不超过50字，用于卡片预览
- detailed_summary: 保留所有关键信息，长度5-20句话，视复杂程度调整。删除冗余表述，但不能遗漏重要数据和因果关系"""

# Agent name → instruction prompt mapping
AGENT_PROMPTS: Dict[str, str] = {
    "entity_extractor": ENTITY_EXTRACTION_PROMPT,
    "summary_sentiment": SUMMARY_SENTIMENT_PROMPT,
}

# Agent name → fallback purpose chain for model resolution
AGENT_PURPOSE_CHAINS: Dict[str, list] = {
    "entity_extractor": ["news_entity", "phase2_layer2_analysis", "news_filter"],
    "summary_sentiment": ["news_summary", "phase2_layer2_analysis", "news_filter"],
}

# ---------------------------------------------------------------------------
# Per-agent JSON Schema for response_format (strict mode)
# ---------------------------------------------------------------------------
# NOTE: Field names deliberately avoid "reason" which triggers DeepSeek's
# chain-of-thought reasoning mode, inflating output tokens and latency.

AGENT_SCHEMAS: Dict[str, Dict] = {
    "entity_extractor": {
        "type": "json_schema",
        "json_schema": {
            "name": "entity_result",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "entities": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "entity": {"type": "string"},
                                "type": {"type": "string", "enum": ["stock", "index", "macro"]},
                                "company_name": {"type": "string"},
                                "relation": {
                                    "type": "string",
                                    "enum": ["direct", "industry_peer", "supply_chain",
                                             "competitor", "beneficiary", "subsidiary"],
                                },
                                "score": {"type": "number"},
                            },
                            "required": ["entity", "type", "company_name", "relation", "score"],
                            "additionalProperties": False,
                        },
                    },
                    "themes": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "primary_market": {
                        "type": "string",
                        "enum": ["cn", "hk", "us"],
                    },
                },
                "required": ["entities", "themes", "primary_market"],
                "additionalProperties": False,
            },
        },
    },
    "summary_sentiment": {
        "type": "json_schema",
        "json_schema": {
            "name": "summary_sentiment_result",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "sentiment": {
                        "type": "string",
                        "enum": ["bullish", "bearish", "neutral"],
                    },
                    "industry_tags": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "event_tags": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "investment_summary": {"type": "string"},
                    "detailed_summary": {"type": "string"},
                },
                "required": ["sentiment", "industry_tags", "event_tags",
                             "investment_summary", "detailed_summary"],
                "additionalProperties": False,
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Tool-call argument helpers
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class MultiAgentResult:
    """Combined result from 2-agent analysis."""

    decision: str  # always "keep" for articles that reach this service
    entities: List[Dict[str, Any]]
    sentiment: str
    industry_tags: List[str]
    event_tags: List[str]
    investment_summary: str
    detailed_summary: str
    analysis_report: str  # Always "" — deep analysis is now on-demand
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
    """2-Agent parallel analysis with shared prompt cache.

    Both agents share the same system message + article context which
    is marked with ``cache_control={"type": "ephemeral"}`` so that the
    first agent populates the prompt cache and the second agent reads
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
        """Run 2 agents in parallel with shared prompt cache.

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
        # 1. Resolve per-agent model configs (with fallback chain)
        # ------------------------------------------------------------------
        from app.services.settings_service import get_settings_service

        settings_service = get_settings_service()

        agent_configs: Dict[str, Any] = {}  # agent_name -> ResolvedModelConfig
        for agent_name in AGENT_PROMPTS:
            chain = AGENT_PURPOSE_CHAINS.get(
                agent_name, ["phase2_layer2_analysis", "news_filter"]
            )
            try:
                agent_configs[agent_name] = (
                    await settings_service.resolve_model_with_fallback(db, chain)
                )
            except ValueError as e:
                logger.error(
                    "Cannot resolve model for agent '%s': %s", agent_name, e
                )
                return self._empty_result(error_reason=str(e))

        # Log model assignment summary (CRITICAL for debugging)
        model_map = {name: cfg.model for name, cfg in agent_configs.items()}
        logger.info(
            "L3深度分析模型分配: %s",
            model_map,
        )

        # Check at least one has a valid API key
        first_config = next(iter(agent_configs.values()))
        if not first_config.api_key:
            logger.error(
                "MultiAgentFilterService: no API key available"
            )
            return self._empty_result(
                error_reason="No API key configured for news analysis"
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
        # 3. Group agents by model for prompt cache sharing
        # ------------------------------------------------------------------
        # Agents using the same model+endpoint share the cache prefix.
        model_groups: Dict[str, list] = defaultdict(list)
        for agent_name in AGENT_PROMPTS:
            cfg = agent_configs[agent_name]
            cache_key = f"{cfg.model}|{cfg.base_url or ''}"
            model_groups[cache_key].append(agent_name)

        if len(model_groups) > 1:
            logger.warning(
                "L3 Prompt缓存效率降低: %d个模型组（不同模型间无法共享缓存）: %s",
                len(model_groups),
                {k: v for k, v in model_groups.items()},
            )

        # ------------------------------------------------------------------
        # 4. Run 2 agents in parallel (json_schema strict mode, no tools)
        # ------------------------------------------------------------------
        # All agents sharing the same model+endpoint share the cached
        # message prefix (system + article context).  Each agent has its
        # own json_schema in response_format -- this does NOT affect the
        # cache key, so different schemas still share cache.

        tasks = []
        for name, prompt in AGENT_PROMPTS.items():
            tasks.append(
                self._run_agent(
                    agent_name=name,
                    base_messages=base_messages,
                    instruction=prompt,
                    model_config=agent_configs[name],
                    response_schema=AGENT_SCHEMAS.get(name),
                    db=db if name == "entity_extractor" else None,
                )
            )

        logger.info(
            "MultiAgentFilterService: starting 2 agents for symbol=%s title=%s",
            symbol,
            title[:80],
        )

        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        # ------------------------------------------------------------------
        # 5. Collect agent responses, handle failures
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
        # 6. Merge results
        # ------------------------------------------------------------------
        merged = self._merge_agent_results(agent_responses)

        # ------------------------------------------------------------------
        # 6b. Post-merge entity ticker validation (safety net)
        # Normally done inside submit_entities skill, but needed for the
        # fallback path where _run_agent() is used without tools.
        # ------------------------------------------------------------------
        if merged.entities:
            try:
                from app.services.news_layer3_analysis_service import (
                    resolve_entity_tickers,
                )
                from app.services.stock_list_service import get_stock_list_service

                stock_list_svc = await get_stock_list_service()
                merged.entities = resolve_entity_tickers(
                    merged.entities, stock_list_svc
                )
            except Exception as e:
                logger.warning(
                    "Entity ticker resolution failed (non-fatal): %s", e
                )

        # ------------------------------------------------------------------
        # 7. Compute cache statistics
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
            "model_groups": {k: v for k, v in model_groups.items()},
            "per_agent": {
                name: {
                    "success": resp.success,
                    "elapsed_ms": round(resp.elapsed_ms, 1),
                    "prompt_tokens": resp.prompt_tokens,
                    "cached_tokens": resp.cached_tokens,
                    "completion_tokens": resp.completion_tokens,
                    "model": agent_configs[name].model,
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
            "tokens=%d (cached=%d, hit_rate=%.1f%%), "
            "models=%d, elapsed=%.0fms",
            symbol,
            total_tokens,
            total_cached,
            cache_hit_rate * 100,
            len(model_groups),
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
        response_schema: Optional[Dict] = None,
        db: Optional[Any] = None,
    ) -> _AgentResponse:
        """Run a single agent with shared prompt cache + json_schema output.

        All agents share the same message prefix (system + article context)
        so prompt caching works across agents.  Each agent has its own
        ``response_format`` json_schema — this does NOT affect the cache
        key, so different schemas still share the cached prefix.

        For entity_extractor, the parsed JSON is post-processed through
        ``SubmitEntitiesSkill.execute()`` for ticker resolution and theme
        expansion.

        Args:
            agent_name: Identifier for this agent (for logging/stats).
            base_messages: Shared system + article context messages
                (with cache_control already set).
            instruction: Agent-specific instruction prompt.
            model_config: ResolvedModelConfig from settings_service.
            response_schema: json_schema response_format dict for this agent.
            db: Database session (only needed for entity_extractor).

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
            response_format=response_schema,
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

            data: dict = {}
            raw_content = response.content or ""

            # ── Parse JSON from response content ──
            if raw_content.strip():
                try:
                    data = json.loads(raw_content)
                except json.JSONDecodeError:
                    # json_schema should guarantee valid JSON, but fall back
                    # to multi-strategy extraction if needed
                    try:
                        data = extract_json_from_response(raw_content)
                    except (ValueError, Exception) as json_err:
                        logger.warning(
                            "Agent '%s' JSON extraction failed: %s (%d chars)",
                            agent_name, json_err, len(raw_content),
                        )

            if not data and raw_content.strip():
                data = {"_raw_content": raw_content.strip()[:5000]}
                logger.info(
                    "Agent '%s': stored raw content as fallback (%d chars)",
                    agent_name, len(raw_content),
                )

            # ── Entity extractor post-processing ──
            # Run ticker resolution + theme expansion via SubmitEntitiesSkill
            if agent_name == "entity_extractor" and data and "entities" in data:
                data = await self._post_process_entities(data, db)

            logger.debug(
                "Agent '%s' completed: %d prompt (cached=%d), "
                "%d completion, %.0fms, keys=%s",
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
                raw_content=raw_content[:5000],
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

    @staticmethod
    async def _post_process_entities(
        data: Dict[str, Any], db: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Run ticker resolution + theme expansion on entity extractor output.

        Delegates to SubmitEntitiesSkill.execute() which handles:
        1. Normalize & verify ticker symbols via StockListService
        2. Expand industry themes via knowledge-base vector search
        3. Deduplicate and cap at 15 entities
        """
        try:
            from app.skills.knowledge.submit_entities import SubmitEntitiesSkill

            skill = SubmitEntitiesSkill()
            skill_result = await skill.safe_execute(
                timeout=60.0,
                entities=data.get("entities", []),
                themes=data.get("themes", []),
                primary_market=data.get("primary_market", ""),
                db=db,
            )
            if skill_result.success and skill_result.data:
                return skill_result.data
            else:
                logger.warning(
                    "Entity post-processing failed: %s, using raw data",
                    skill_result.error,
                )
                return data
        except Exception as e:
            logger.warning(
                "Entity post-processing error: %s, using raw data", e,
            )
            return data

    def _merge_agent_results(
        self,
        agent_responses: Dict[str, _AgentResponse],
    ) -> MultiAgentResult:
        """Merge outputs from 2 agents into a single result."""
        # --- Entity Extractor ---
        entity_data = agent_responses.get("entity_extractor")
        raw_entities: List[Any] = []
        if entity_data and entity_data.success:
            raw_entities = entity_data.data.get("entities", [])
        entities = validate_entities(raw_entities, max_entities=15)

        # --- Summary & Sentiment (merged agent) ---
        ss_data = agent_responses.get("summary_sentiment")
        sentiment = "neutral"
        industry_tags: List[str] = []
        event_tags: List[str] = []
        investment_summary = ""
        detailed_summary = ""

        if ss_data and ss_data.success:
            raw_sentiment = ss_data.data.get("sentiment", "neutral")
            if raw_sentiment in ("bullish", "bearish", "neutral"):
                sentiment = raw_sentiment
            industry_tags = ss_data.data.get("industry_tags", [])[:5]
            event_tags = ss_data.data.get("event_tags", [])[:5]

            # Validate tag values
            valid_industry = {
                "tech", "finance", "healthcare", "energy", "consumer",
                "industrial", "materials", "utilities", "realestate", "telecom",
            }
            valid_events = {
                "earnings", "merger", "ipo", "regulatory", "executive",
                "product", "lawsuit", "dividend", "buyback", "guidance", "macro",
            }
            industry_tags = [t for t in industry_tags if t in valid_industry]
            event_tags = [t for t in event_tags if t in valid_events]

            investment_summary = (
                ss_data.data.get("investment_summary", "") or ""
            )[:500]
            detailed_summary = (
                ss_data.data.get("detailed_summary", "") or ""
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

        return MultiAgentResult(
            decision="keep",
            entities=entities,
            sentiment=sentiment,
            industry_tags=industry_tags,
            event_tags=event_tags,
            investment_summary=investment_summary,
            detailed_summary=detailed_summary,
            analysis_report="",
        )

    @staticmethod
    def _empty_result(error_reason: str = "") -> MultiAgentResult:
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
            cache_stats={
                "total_tokens": 0,
                "cached_tokens": 0,
                "cache_hit_rate": 0.0,
                "error": error_reason,
            },
        )


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
