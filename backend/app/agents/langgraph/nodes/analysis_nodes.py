"""Analysis nodes for LangGraph workflow.

This module contains the four primary analysis nodes:
- fundamental_node: Fundamental/valuation analysis
- technical_node: Technical/chart analysis
- sentiment_node: Market sentiment analysis
- news_node: News impact analysis

Each node:
1. Loads instructions from prompt templates
2. Prepares data using the Skills system
3. Calls LLM via llm_config
4. Extracts and validates structured output
5. Returns AgentAnalysisResult
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.agents.langgraph.state import AnalysisState
from app.agents.langgraph.utils.json_extractor import (
    extract_json_from_response,
    extract_structured_data,
    safe_json_extract,
)
from app.core.llm import get_analysis_langchain_model
from app.prompts.loader import load_instructions
from app.schemas.agent_analysis import (
    ActionRecommendation,
    AgentAnalysisResult,
    AnalysisConfidence,
    FundamentalAnalysisResult,
    FundamentalMetrics,
    KeyInsight,
    NewsAnalysisResult,
    NewsItem,
    SentimentAnalysisResult,
    SentimentLevel,
    SentimentSource,
    SupportResistanceLevel,
    TechnicalAnalysisResult,
    TechnicalIndicators,
    TrendDirection,
    ValuationAssessment,
)
from app.services.token_service import count_tokens
from app.skills.base import SkillResult

logger = logging.getLogger(__name__)

# Default timeout for LLM calls (seconds)
LLM_TIMEOUT = 60


# =============================================================================
# Helper functions for data formatting
# =============================================================================


def _format_number(value: Optional[float]) -> str:
    """Format a number for display."""
    if value is None:
        return "N/A"
    if abs(value) >= 1e12:
        return f"{value / 1e12:.2f}T"
    if abs(value) >= 1e9:
        return f"{value / 1e9:.2f}B"
    if abs(value) >= 1e6:
        return f"{value / 1e6:.2f}M"
    if abs(value) >= 1e3:
        return f"{value / 1e3:.2f}K"
    return f"{value:.2f}"


def _format_percent(value: Optional[float]) -> str:
    """Format a percentage for display (assumes decimal input)."""
    if value is None:
        return "N/A"
    return f"{value * 100:.2f}%"


def _format_ratio(value: Optional[float]) -> str:
    """Format a ratio for display."""
    if value is None:
        return "N/A"
    return f"{value:.2f}"


def _format_price(value: Optional[float]) -> str:
    """Format a price for display."""
    if value is None:
        return "N/A"
    return f"${value:.2f}"


# =============================================================================
# Skills infrastructure
# =============================================================================

# Agent -> Skills pre-configuration mapping
AGENT_SKILLS: Dict[str, List[str]] = {
    "fundamental": [
        "get_stock_quote", "get_stock_info", "get_stock_financials",
        "get_institutional_holders", "get_fund_holdings_cn",
        "get_northbound_holding", "get_sector_industry",
    ],
    "technical": [
        "get_stock_quote", "get_stock_history",
    ],
    "sentiment": [
        "get_stock_quote", "get_stock_history", "get_analyst_ratings",
        "get_news", "get_market_context",
    ],
    "news": [
        "get_news", "get_stock_quote",
    ],
}

# Skills to skip based on market type
_CN_ONLY_SKILLS = {"get_fund_holdings_cn", "get_northbound_holding"}
_NON_CN_ONLY_SKILLS = {"get_institutional_holders"}


def _build_skill_kwargs(
    name: str,
    symbol: str,
    market: str,
    agent_type: str,
) -> Dict[str, Any]:
    """Build kwargs for a specific skill based on skill name and agent context."""
    if name == "get_stock_history":
        if agent_type == "sentiment":
            return {"symbol": symbol, "period": "3mo", "interval": "1d"}
        return {"symbol": symbol, "period": "1y", "interval": "1d"}
    elif name == "get_market_context":
        return {}
    elif name == "get_news":
        return {"symbol": symbol, "limit": 10}
    elif name == "get_sector_industry":
        return {"symbol": symbol, "market": market}
    elif name in ("get_fund_holdings_cn", "get_northbound_holding"):
        return {"symbol": symbol}
    else:
        return {"symbol": symbol}


async def _execute_agent_skills(
    agent_type: str,
    symbol: str,
    market: str,
) -> Dict[str, SkillResult]:
    """Execute all pre-configured skills for an agent in parallel."""
    from app.skills.registry import get_skill_registry

    registry = get_skill_registry()
    skill_names = list(AGENT_SKILLS.get(agent_type, []))

    # Market-aware filtering
    if market in ("CN", "A"):
        skill_names = [s for s in skill_names if s not in _NON_CN_ONLY_SKILLS]
    else:
        skill_names = [s for s in skill_names if s not in _CN_ONLY_SKILLS]

    # Build kwargs for each skill
    tasks = {}
    for name in skill_names:
        skill = registry.get(name)
        if skill is None:
            continue
        kwargs = _build_skill_kwargs(name, symbol, market, agent_type)
        tasks[name] = skill.safe_execute(timeout=15.0, **kwargs)

    # Execute in parallel
    results: Dict[str, SkillResult] = {}
    if tasks:
        task_names = list(tasks.keys())
        task_coros = list(tasks.values())
        done = await asyncio.gather(*task_coros, return_exceptions=True)
        for name, result in zip(task_names, done):
            if isinstance(result, Exception):
                results[name] = SkillResult(success=False, error=str(result))
            else:
                results[name] = result

    # Post-processing: chain dependent skills
    if agent_type == "technical":
        history_result = results.get("get_stock_history")
        if history_result and history_result.success and history_result.data:
            bars = history_result.data.get("bars", [])
            if bars:
                indicator_skill = registry.get("calculate_technical_indicators")
                summary_skill = registry.get("calculate_history_summary")

                post_tasks = {}
                if indicator_skill:
                    post_tasks["calculate_technical_indicators"] = indicator_skill.safe_execute(bars=bars)
                if summary_skill:
                    post_tasks["calculate_history_summary"] = summary_skill.safe_execute(bars=bars)

                if post_tasks:
                    post_names = list(post_tasks.keys())
                    post_coros = list(post_tasks.values())
                    post_done = await asyncio.gather(*post_coros, return_exceptions=True)
                    for n, r in zip(post_names, post_done):
                        if isinstance(r, Exception):
                            results[n] = SkillResult(success=False, error=str(r))
                        else:
                            results[n] = r

    elif agent_type == "news":
        news_result = results.get("get_news")
        if news_result and news_result.success and news_result.data:
            scoring_skill = registry.get("score_news_articles")
            if scoring_skill:
                scored = await scoring_skill.safe_execute(articles=news_result.data)
                results["score_news_articles"] = scored

    logger.debug(
        "Agent %s skills completed: %d/%d succeeded",
        agent_type,
        sum(1 for r in results.values() if r.success),
        len(results),
    )

    return results


# =============================================================================
# Fundamental Analysis Node
# =============================================================================


def _build_fundamental_data_prompt(
    symbol: str,
    market: str,
    skill_results: Dict[str, SkillResult],
    language: str,
) -> str:
    """Build data section for fundamental analysis prompt."""
    sections = []

    # Quote section
    quote_result = skill_results.get("get_stock_quote")
    if quote_result and quote_result.success and quote_result.data:
        quote = quote_result.data
        if language == "zh":
            sections.append(f"""## 当前市场数据
- 当前价格: {_format_price(quote.get('price'))}
- 涨跌幅: {quote.get('change_percent', 'N/A')}%
- 成交量: {_format_number(quote.get('volume'))}
- 市值: {_format_number(quote.get('market_cap'))}
""")
        else:
            sections.append(f"""## Current Market Data
- Current Price: {_format_price(quote.get('price'))}
- Change: {quote.get('change_percent', 'N/A')}%
- Volume: {_format_number(quote.get('volume'))}
- Market Cap: {_format_number(quote.get('market_cap'))}
""")

    # Financials section
    financials_result = skill_results.get("get_stock_financials")
    if financials_result and financials_result.success and financials_result.data:
        financials = financials_result.data
        if language == "zh":
            sections.append(f"""## 财务指标
### 估值
- 市盈率 (TTM): {_format_ratio(financials.get('pe_ratio'))}
- 前瞻市盈率: {_format_ratio(financials.get('forward_pe'))}
- 市净率: {_format_ratio(financials.get('price_to_book'))}

### 盈利能力
- 每股收益 (TTM): {_format_price(financials.get('eps'))}
- 利润率: {_format_percent(financials.get('profit_margin'))}
- 净资产收益率 (ROE): {_format_percent(financials.get('roe'))}

### 成长性
- 营收: {_format_number(financials.get('revenue'))}
- 股息收益率: {_format_percent(financials.get('dividend_yield'))}

### 资产负债
- 负债权益比: {_format_ratio(financials.get('debt_to_equity'))}
""")
        else:
            sections.append(f"""## Financial Metrics
### Valuation
- P/E Ratio (TTM): {_format_ratio(financials.get('pe_ratio'))}
- Forward P/E: {_format_ratio(financials.get('forward_pe'))}
- Price to Book: {_format_ratio(financials.get('price_to_book'))}

### Profitability
- EPS (TTM): {_format_price(financials.get('eps'))}
- Profit Margin: {_format_percent(financials.get('profit_margin'))}
- ROE: {_format_percent(financials.get('roe'))}

### Growth & Income
- Revenue: {_format_number(financials.get('revenue'))}
- Dividend Yield: {_format_percent(financials.get('dividend_yield'))}

### Balance Sheet
- Debt to Equity: {_format_ratio(financials.get('debt_to_equity'))}
""")

    # Company info section
    info_result = skill_results.get("get_stock_info")
    if info_result and info_result.success and info_result.data:
        info = info_result.data
        if language == "zh":
            sections.append(f"""## 公司信息
- 名称: {info.get('name', 'N/A')}
- 行业: {info.get('sector', 'N/A')}
- 细分行业: {info.get('industry', 'N/A')}
- 员工数: {info.get('employees', 'N/A')}
""")
        else:
            sections.append(f"""## Company Information
- Name: {info.get('name', 'N/A')}
- Sector: {info.get('sector', 'N/A')}
- Industry: {info.get('industry', 'N/A')}
- Employees: {info.get('employees', 'N/A')}
""")

    return "\n".join(sections) if sections else (
        "数据有限。请基于股票代码进行分析。" if language == "zh"
        else "Limited data available. Please analyze based on the stock symbol."
    )


async def fundamental_node(state: AnalysisState) -> Dict[str, Any]:
    """
    Fundamental analysis node.

    Analyzes valuation, financial health, and profitability metrics.
    """
    start_time = time.time()
    symbol = state["symbol"]
    market = state["market"]
    language = state.get("language", "en")

    logger.info(f"Fundamental analysis started for {symbol} ({market})")

    try:
        # 1. Load instructions
        instruction_file = "fundamental_instructions.md" if language == "en" else "fundamental_instructions_zh.md"
        try:
            instructions = load_instructions(instruction_file, subdirectory="templates/analysis")
        except FileNotFoundError:
            # Fall back to a minimal instruction set
            if language == "zh":
                instructions = """你是一位专业的基本面分析师。
分析给定的股票数据，评估其估值、盈利能力和财务健康状况。
以JSON格式输出结果。"""
            else:
                instructions = """You are a professional fundamental analyst.
Analyze the given stock data, evaluate its valuation, profitability, and financial health.
Output results in JSON format."""

        # 2. Prepare data via Skills
        skill_results = await _execute_agent_skills("fundamental", symbol, market)

        # 3. Build prompt
        data_section = _build_fundamental_data_prompt(symbol, market, skill_results, language)

        if language == "zh":
            user_prompt = f"""# 基本面分析请求

**股票代码**: {symbol}
**市场**: {market}

{data_section}

请分析这只股票的基本面，并以JSON格式输出结果。
"""
        else:
            user_prompt = f"""# Fundamental Analysis Request

**Stock Symbol**: {symbol}
**Market**: {market}

{data_section}

Please analyze the fundamentals of this stock and output results in JSON format.
"""

        # 4. Call LLM
        try:
            llm = await get_analysis_langchain_model()
            messages = [
                {"role": "system", "content": instructions},
                {"role": "user", "content": user_prompt},
            ]

            response = await asyncio.wait_for(
                llm.ainvoke(messages),
                timeout=LLM_TIMEOUT,
            )
            content = response.content
        except asyncio.TimeoutError:
            logger.error(f"Fundamental analysis timeout for {symbol}")
            return {
                "fundamental": AgentAnalysisResult(
                    agent_type="fundamental",
                    symbol=symbol,
                    market=market,
                    success=False,
                    error="Analysis timeout",
                    latency_ms=int((time.time() - start_time) * 1000),
                ),
                "errors": [f"Fundamental analysis timeout for {symbol}"],
            }
        except Exception as e:
            logger.error(f"Fundamental LLM call failed for {symbol}: {e}")
            return {
                "fundamental": AgentAnalysisResult(
                    agent_type="fundamental",
                    symbol=symbol,
                    market=market,
                    success=False,
                    error=str(e),
                    latency_ms=int((time.time() - start_time) * 1000),
                ),
                "errors": [f"Fundamental LLM error: {e}"],
            }

        # 5. Parse result
        json_data = safe_json_extract(content, {})

        # Try to validate with schema
        fundamental_result = extract_structured_data(
            content, FundamentalAnalysisResult, strict=False
        )

        latency_ms = int((time.time() - start_time) * 1000)
        tokens_used = count_tokens(instructions + user_prompt + content)

        logger.info(f"Fundamental analysis completed for {symbol} in {latency_ms}ms")

        return {
            "fundamental": AgentAnalysisResult(
                agent_type="fundamental",
                symbol=symbol,
                market=market,
                success=True,
                fundamental=fundamental_result,
                raw_content=content,
                raw_data=json_data,
                latency_ms=latency_ms,
                tokens_used=tokens_used,
            ),
        }

    except Exception as e:
        logger.exception(f"Fundamental analysis error for {symbol}: {e}")
        return {
            "fundamental": AgentAnalysisResult(
                agent_type="fundamental",
                symbol=symbol,
                market=market,
                success=False,
                error=str(e),
                latency_ms=int((time.time() - start_time) * 1000),
            ),
            "errors": [f"Fundamental analysis error: {e}"],
        }


# =============================================================================
# Technical Analysis Node
# =============================================================================


def _build_technical_data_prompt(
    symbol: str,
    market: str,
    skill_results: Dict[str, SkillResult],
    language: str,
) -> str:
    """Build data section for technical analysis prompt."""
    sections = []

    # Quote
    quote_result = skill_results.get("get_stock_quote")
    if quote_result and quote_result.success and quote_result.data:
        quote = quote_result.data
        if language == "zh":
            sections.append(f"""## 当前行情
- 价格: {_format_price(quote.get('price'))}
- 涨跌幅: {quote.get('change_percent', 'N/A')}%
- 成交量: {_format_number(quote.get('volume'))}
""")
        else:
            sections.append(f"""## Current Quote
- Price: {_format_price(quote.get('price'))}
- Change: {quote.get('change_percent', 'N/A')}%
- Volume: {_format_number(quote.get('volume'))}
""")

    # Indicators
    indicators_result = skill_results.get("calculate_technical_indicators")
    if indicators_result and indicators_result.success and indicators_result.data:
        indicators = indicators_result.data
        if language == "zh":
            sections.append(f"""## 技术指标
- SMA(20): {_format_price(indicators.get('sma_20'))}
- SMA(50): {_format_price(indicators.get('sma_50'))}
- SMA(200): {_format_price(indicators.get('sma_200'))}
- RSI(14): {_format_ratio(indicators.get('rsi_14'))}
- MACD: {_format_ratio(indicators.get('macd'))}
- MACD信号线: {_format_ratio(indicators.get('macd_signal'))}
- 成交量比: {_format_ratio(indicators.get('volume_ratio'))}
- 20日波动率: {_format_ratio(indicators.get('volatility_20d'))}%
""")
        else:
            sections.append(f"""## Technical Indicators
- SMA(20): {_format_price(indicators.get('sma_20'))}
- SMA(50): {_format_price(indicators.get('sma_50'))}
- SMA(200): {_format_price(indicators.get('sma_200'))}
- RSI(14): {_format_ratio(indicators.get('rsi_14'))}
- MACD: {_format_ratio(indicators.get('macd'))}
- MACD Signal: {_format_ratio(indicators.get('macd_signal'))}
- Volume Ratio: {_format_ratio(indicators.get('volume_ratio'))}
- 20-day Volatility: {_format_ratio(indicators.get('volatility_20d'))}%
""")

    # Summary
    summary_result = skill_results.get("calculate_history_summary")
    if summary_result and summary_result.success and summary_result.data:
        summary = summary_result.data
        if language == "zh":
            sections.append(f"""## 价格区间
- 52周高点: {_format_price(summary.get('high_52w'))}
- 52周低点: {_format_price(summary.get('low_52w'))}
- 周涨幅: {_format_ratio(summary.get('change_1w'))}%
- 月涨幅: {_format_ratio(summary.get('change_1m'))}%
- 季涨幅: {_format_ratio(summary.get('change_3m'))}%
""")
        else:
            sections.append(f"""## Price Range
- 52-week High: {_format_price(summary.get('high_52w'))}
- 52-week Low: {_format_price(summary.get('low_52w'))}
- 1-week Change: {_format_ratio(summary.get('change_1w'))}%
- 1-month Change: {_format_ratio(summary.get('change_1m'))}%
- 3-month Change: {_format_ratio(summary.get('change_3m'))}%
""")

    return "\n".join(sections) if sections else (
        "技术数据有限。" if language == "zh" else "Limited technical data available."
    )


async def technical_node(state: AnalysisState) -> Dict[str, Any]:
    """
    Technical analysis node.

    Analyzes price trends, patterns, and technical indicators.
    """
    start_time = time.time()
    symbol = state["symbol"]
    market = state["market"]
    language = state.get("language", "en")

    logger.info(f"Technical analysis started for {symbol} ({market})")

    try:
        # 1. Load instructions
        instruction_file = "technical_instructions.md" if language == "en" else "technical_instructions_zh.md"
        try:
            instructions = load_instructions(instruction_file, subdirectory="templates/analysis")
        except FileNotFoundError:
            if language == "zh":
                instructions = """你是一位专业的技术分析师。
分析给定的价格数据和技术指标，评估趋势、支撑/阻力位和动量。
以JSON格式输出结果。"""
            else:
                instructions = """You are a professional technical analyst.
Analyze the given price data and technical indicators, evaluate trends, support/resistance levels, and momentum.
Output results in JSON format."""

        # 2. Prepare data via Skills
        skill_results = await _execute_agent_skills("technical", symbol, market)

        # 3. Build prompt
        data_section = _build_technical_data_prompt(symbol, market, skill_results, language)

        if language == "zh":
            user_prompt = f"""# 技术分析请求

**股票代码**: {symbol}
**市场**: {market}

{data_section}

请分析这只股票的技术面，并以JSON格式输出结果。
"""
        else:
            user_prompt = f"""# Technical Analysis Request

**Stock Symbol**: {symbol}
**Market**: {market}

{data_section}

Please analyze the technicals of this stock and output results in JSON format.
"""

        # 4. Call LLM
        try:
            llm = await get_analysis_langchain_model()
            messages = [
                {"role": "system", "content": instructions},
                {"role": "user", "content": user_prompt},
            ]

            response = await asyncio.wait_for(
                llm.ainvoke(messages),
                timeout=LLM_TIMEOUT,
            )
            content = response.content
        except asyncio.TimeoutError:
            logger.error(f"Technical analysis timeout for {symbol}")
            return {
                "technical": AgentAnalysisResult(
                    agent_type="technical",
                    symbol=symbol,
                    market=market,
                    success=False,
                    error="Analysis timeout",
                    latency_ms=int((time.time() - start_time) * 1000),
                ),
                "errors": [f"Technical analysis timeout for {symbol}"],
            }
        except Exception as e:
            logger.error(f"Technical LLM call failed for {symbol}: {e}")
            return {
                "technical": AgentAnalysisResult(
                    agent_type="technical",
                    symbol=symbol,
                    market=market,
                    success=False,
                    error=str(e),
                    latency_ms=int((time.time() - start_time) * 1000),
                ),
                "errors": [f"Technical LLM error: {e}"],
            }

        # 5. Parse result
        json_data = safe_json_extract(content, {})
        technical_result = extract_structured_data(
            content, TechnicalAnalysisResult, strict=False
        )

        latency_ms = int((time.time() - start_time) * 1000)
        tokens_used = count_tokens(instructions + user_prompt + content)

        logger.info(f"Technical analysis completed for {symbol} in {latency_ms}ms")

        return {
            "technical": AgentAnalysisResult(
                agent_type="technical",
                symbol=symbol,
                market=market,
                success=True,
                technical=technical_result,
                raw_content=content,
                raw_data=json_data,
                latency_ms=latency_ms,
                tokens_used=tokens_used,
            ),
        }

    except Exception as e:
        logger.exception(f"Technical analysis error for {symbol}: {e}")
        return {
            "technical": AgentAnalysisResult(
                agent_type="technical",
                symbol=symbol,
                market=market,
                success=False,
                error=str(e),
                latency_ms=int((time.time() - start_time) * 1000),
            ),
            "errors": [f"Technical analysis error: {e}"],
        }


# =============================================================================
# Sentiment Analysis Node
# =============================================================================


def _build_sentiment_data_prompt(
    symbol: str,
    market: str,
    skill_results: Dict[str, SkillResult],
    language: str,
) -> str:
    """Build data section for sentiment analysis prompt."""
    sections = []

    # Quote
    quote_result = skill_results.get("get_stock_quote")
    if quote_result and quote_result.success and quote_result.data:
        quote = quote_result.data
        if language == "zh":
            sections.append(f"""## 市场表现
- 当前价格: {_format_price(quote.get('price'))}
- 涨跌幅: {quote.get('change_percent', 'N/A')}%
""")
        else:
            sections.append(f"""## Market Performance
- Current Price: {_format_price(quote.get('price'))}
- Change: {quote.get('change_percent', 'N/A')}%
""")

    # Analyst ratings
    analyst_result = skill_results.get("get_analyst_ratings")
    if analyst_result and analyst_result.success and analyst_result.data:
        analyst = analyst_result.data
        if language == "zh":
            sections.append(f"""## 分析师评级
- 推荐: {analyst.get('recommendation', 'N/A')}
- 目标价: {_format_price(analyst.get('target_price'))}
- 评级人数: {analyst.get('number_of_analysts', 'N/A')}
""")
        else:
            sections.append(f"""## Analyst Ratings
- Recommendation: {analyst.get('recommendation', 'N/A')}
- Target Price: {_format_price(analyst.get('target_price'))}
- Number of Analysts: {analyst.get('number_of_analysts', 'N/A')}
""")

    # News headlines
    news_result = skill_results.get("get_news")
    if news_result and news_result.success and news_result.data:
        news = news_result.data
        if language == "zh":
            sections.append("## 近期新闻")
            for article in news[:5]:
                sections.append(f"- [{article.get('source', '未知')}] {article.get('title', '')}")
        else:
            sections.append("## Recent News")
            for article in news[:5]:
                sections.append(f"- [{article.get('source', 'Unknown')}] {article.get('title', '')}")

    # Market context
    ctx_result = skill_results.get("get_market_context")
    if ctx_result and ctx_result.success and ctx_result.data:
        ctx = ctx_result.data
        if language == "zh":
            sections.append(f"""## 市场环境
- 大盘趋势: {ctx.get('market_trend', 'N/A')}
""")
        else:
            sections.append(f"""## Market Context
- Market Trend: {ctx.get('market_trend', 'N/A')}
""")

    return "\n".join(sections) if sections else (
        "情绪数据有限。" if language == "zh" else "Limited sentiment data available."
    )


async def sentiment_node(state: AnalysisState) -> Dict[str, Any]:
    """
    Sentiment analysis node.

    Analyzes market sentiment, news tone, and investor mood.
    """
    start_time = time.time()
    symbol = state["symbol"]
    market = state["market"]
    language = state.get("language", "en")

    logger.info(f"Sentiment analysis started for {symbol} ({market})")

    try:
        # 1. Load instructions
        instruction_file = "sentiment_instructions.md" if language == "en" else "sentiment_instructions_zh.md"
        try:
            instructions = load_instructions(instruction_file, subdirectory="templates/analysis")
        except FileNotFoundError:
            if language == "zh":
                instructions = """你是一位专业的市场情绪分析师。
分析给定的市场数据、新闻和分析师评级，评估整体市场情绪。
以JSON格式输出结果。"""
            else:
                instructions = """You are a professional market sentiment analyst.
Analyze the given market data, news, and analyst ratings to evaluate overall market sentiment.
Output results in JSON format."""

        # 2. Prepare data via Skills
        skill_results = await _execute_agent_skills("sentiment", symbol, market)

        # 3. Build prompt
        data_section = _build_sentiment_data_prompt(symbol, market, skill_results, language)

        if language == "zh":
            user_prompt = f"""# 情绪分析请求

**股票代码**: {symbol}
**市场**: {market}

{data_section}

请分析这只股票的市场情绪，并以JSON格式输出结果。
"""
        else:
            user_prompt = f"""# Sentiment Analysis Request

**Stock Symbol**: {symbol}
**Market**: {market}

{data_section}

Please analyze the market sentiment for this stock and output results in JSON format.
"""

        # 4. Call LLM
        try:
            llm = await get_analysis_langchain_model()
            messages = [
                {"role": "system", "content": instructions},
                {"role": "user", "content": user_prompt},
            ]

            response = await asyncio.wait_for(
                llm.ainvoke(messages),
                timeout=LLM_TIMEOUT,
            )
            content = response.content
        except asyncio.TimeoutError:
            logger.error(f"Sentiment analysis timeout for {symbol}")
            return {
                "sentiment": AgentAnalysisResult(
                    agent_type="sentiment",
                    symbol=symbol,
                    market=market,
                    success=False,
                    error="Analysis timeout",
                    latency_ms=int((time.time() - start_time) * 1000),
                ),
                "errors": [f"Sentiment analysis timeout for {symbol}"],
            }
        except Exception as e:
            logger.error(f"Sentiment LLM call failed for {symbol}: {e}")
            return {
                "sentiment": AgentAnalysisResult(
                    agent_type="sentiment",
                    symbol=symbol,
                    market=market,
                    success=False,
                    error=str(e),
                    latency_ms=int((time.time() - start_time) * 1000),
                ),
                "errors": [f"Sentiment LLM error: {e}"],
            }

        # 5. Parse result
        json_data = safe_json_extract(content, {})
        sentiment_result = extract_structured_data(
            content, SentimentAnalysisResult, strict=False
        )

        latency_ms = int((time.time() - start_time) * 1000)
        tokens_used = count_tokens(instructions + user_prompt + content)

        logger.info(f"Sentiment analysis completed for {symbol} in {latency_ms}ms")

        return {
            "sentiment": AgentAnalysisResult(
                agent_type="sentiment",
                symbol=symbol,
                market=market,
                success=True,
                sentiment=sentiment_result,
                raw_content=content,
                raw_data=json_data,
                latency_ms=latency_ms,
                tokens_used=tokens_used,
            ),
        }

    except Exception as e:
        logger.exception(f"Sentiment analysis error for {symbol}: {e}")
        return {
            "sentiment": AgentAnalysisResult(
                agent_type="sentiment",
                symbol=symbol,
                market=market,
                success=False,
                error=str(e),
                latency_ms=int((time.time() - start_time) * 1000),
            ),
            "errors": [f"Sentiment analysis error: {e}"],
        }


# =============================================================================
# News Analysis Node
# =============================================================================


def _build_news_data_prompt(
    symbol: str,
    market: str,
    skill_results: Dict[str, SkillResult],
    language: str,
) -> str:
    """Build data section for news analysis prompt."""
    # Get scored articles (preferred) or raw news
    scored_result = skill_results.get("score_news_articles")
    if scored_result and scored_result.success and scored_result.data:
        articles = scored_result.data
    else:
        # Fall back to raw news if scoring failed
        news_result = skill_results.get("get_news")
        if news_result and news_result.success and news_result.data:
            articles = news_result.data
        else:
            articles = []

    if not articles:
        if language == "zh":
            return f"未找到 {symbol} 的近期新闻。请提供一般性市场展望。"
        return f"No recent news found for {symbol}. Please provide a general market outlook."

    sections = []

    # Stock context
    ctx_result = skill_results.get("get_stock_quote")
    if ctx_result and ctx_result.success and ctx_result.data:
        ctx = ctx_result.data
        if language == "zh":
            sections.append(f"""## 股票概况
- 价格: {_format_price(ctx.get('price'))}
- 涨跌幅: {ctx.get('change_percent', 'N/A')}%
""")
        else:
            sections.append(f"""## Stock Context
- Price: {_format_price(ctx.get('price'))}
- Change: {ctx.get('change_percent', 'N/A')}%
""")

    # News articles
    if language == "zh":
        sections.append("## 新闻文章")
    else:
        sections.append("## News Articles")

    for i, article in enumerate(articles[:10]):
        title = article.get("title", "No title")
        source = article.get("source", "Unknown")
        published = article.get("publishedAt", "")
        summary = article.get("summary", "")[:300] if article.get("summary") else ""

        if language == "zh":
            sections.append(f"""### {i+1}. {title}
- 来源: {source}
- 时间: {published}
- 摘要: {summary}
""")
        else:
            sections.append(f"""### {i+1}. {title}
- Source: {source}
- Published: {published}
- Summary: {summary}
""")

    return "\n".join(sections)


async def news_node(state: AnalysisState) -> Dict[str, Any]:
    """
    News analysis node.

    Analyzes recent news articles and their potential impact on the stock.
    """
    start_time = time.time()
    symbol = state["symbol"]
    market = state["market"]
    language = state.get("language", "en")

    logger.info(f"News analysis started for {symbol} ({market})")

    try:
        # 1. Load instructions
        instruction_file = "news_instructions.md" if language == "en" else "news_instructions_zh.md"
        try:
            instructions = load_instructions(instruction_file, subdirectory="templates/analysis")
        except FileNotFoundError:
            if language == "zh":
                instructions = """你是一位专业的新闻分析师。
分析给定的新闻文章，评估它们对股价的潜在影响。
以JSON格式输出结果。"""
            else:
                instructions = """You are a professional news analyst.
Analyze the given news articles and evaluate their potential impact on the stock price.
Output results in JSON format."""

        # 2. Prepare data via Skills
        skill_results = await _execute_agent_skills("news", symbol, market)

        # 3. Build prompt
        data_section = _build_news_data_prompt(symbol, market, skill_results, language)

        if language == "zh":
            user_prompt = f"""# 新闻分析请求

**股票代码**: {symbol}
**市场**: {market}

{data_section}

请分析这些新闻对股票的影响，并以JSON格式输出结果。
"""
        else:
            user_prompt = f"""# News Analysis Request

**Stock Symbol**: {symbol}
**Market**: {market}

{data_section}

Please analyze the impact of these news articles on the stock and output results in JSON format.
"""

        # 4. Call LLM
        try:
            llm = await get_analysis_langchain_model()
            messages = [
                {"role": "system", "content": instructions},
                {"role": "user", "content": user_prompt},
            ]

            response = await asyncio.wait_for(
                llm.ainvoke(messages),
                timeout=LLM_TIMEOUT,
            )
            content = response.content
        except asyncio.TimeoutError:
            logger.error(f"News analysis timeout for {symbol}")
            return {
                "news": AgentAnalysisResult(
                    agent_type="news",
                    symbol=symbol,
                    market=market,
                    success=False,
                    error="Analysis timeout",
                    latency_ms=int((time.time() - start_time) * 1000),
                ),
                "errors": [f"News analysis timeout for {symbol}"],
            }
        except Exception as e:
            logger.error(f"News LLM call failed for {symbol}: {e}")
            return {
                "news": AgentAnalysisResult(
                    agent_type="news",
                    symbol=symbol,
                    market=market,
                    success=False,
                    error=str(e),
                    latency_ms=int((time.time() - start_time) * 1000),
                ),
                "errors": [f"News LLM error: {e}"],
            }

        # 5. Parse result
        json_data = safe_json_extract(content, {})
        news_result = extract_structured_data(
            content, NewsAnalysisResult, strict=False
        )

        latency_ms = int((time.time() - start_time) * 1000)
        tokens_used = count_tokens(instructions + user_prompt + content)

        logger.info(f"News analysis completed for {symbol} in {latency_ms}ms")

        return {
            "news": AgentAnalysisResult(
                agent_type="news",
                symbol=symbol,
                market=market,
                success=True,
                news=news_result,
                raw_content=content,
                raw_data=json_data,
                latency_ms=latency_ms,
                tokens_used=tokens_used,
            ),
        }

    except Exception as e:
        logger.exception(f"News analysis error for {symbol}: {e}")
        return {
            "news": AgentAnalysisResult(
                agent_type="news",
                symbol=symbol,
                market=market,
                success=False,
                error=str(e),
                latency_ms=int((time.time() - start_time) * 1000),
            ),
            "errors": [f"News analysis error: {e}"],
        }
