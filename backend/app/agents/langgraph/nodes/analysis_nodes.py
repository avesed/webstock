"""Analysis nodes for LangGraph workflow.

This module contains the four primary analysis nodes:
- fundamental_node: Fundamental/valuation analysis
- technical_node: Technical/chart analysis
- sentiment_node: Market sentiment analysis
- news_node: News impact analysis

Each node:
1. Loads instructions from prompt templates
2. Prepares data using existing services
3. Calls LLM via llm_config
4. Extracts and validates structured output
5. Returns AgentAnalysisResult
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from app.agents.langgraph.state import AnalysisState
from app.agents.langgraph.utils.json_extractor import (
    extract_json_from_response,
    extract_structured_data,
    safe_json_extract,
)
from app.core.llm_config import get_analysis_model_from_settings
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
# Fundamental Analysis Node
# =============================================================================


async def _prepare_fundamental_data(
    symbol: str,
    market: str,
) -> Dict[str, Any]:
    """
    Prepare data for fundamental analysis.

    Fetches company info, financials, quote, and institutional holdings.
    """
    from app.services.providers import get_provider_router
    from app.services.stock_service import get_stock_service

    logger.debug(f"Preparing fundamental data for {symbol} ({market})")

    stock_service = await get_stock_service()
    router = await get_provider_router()

    # Fetch basic data in parallel
    info_task = stock_service.get_info(symbol)
    financials_task = stock_service.get_financials(symbol)
    quote_task = stock_service.get_quote(symbol)

    data = {
        "info": None,
        "financials": None,
        "quote": None,
        "institutional_holders": None,
        "fund_holdings": None,
        "northbound_holding": None,
        "sector_industry": None,
    }

    # Market-specific data fetching
    if market == "US":
        inst_task = router.yfinance.get_institutional_holders(symbol)
        sector_task = router.yfinance.get_sector_industry(symbol)

        results = await asyncio.gather(
            info_task, financials_task, quote_task, inst_task, sector_task,
            return_exceptions=True,
        )

        keys = ["info", "financials", "quote", "institutional_holders", "sector_industry"]
        success_count = 0
        for i, key in enumerate(keys):
            if not isinstance(results[i], Exception):
                data[key] = results[i]
                success_count += 1
            else:
                logger.warning(f"Failed to get {key} for {symbol}: {results[i]}")

        logger.debug(f"Fundamental data preparation complete for {symbol}: {success_count}/{len(keys)} sources succeeded")

    elif market in ("CN", "A"):
        stock_code = symbol.split(".")[0]
        fund_task = router.akshare.get_fund_holdings_cn(stock_code)
        northbound_task = router.akshare.get_northbound_holding(stock_code, days=30)
        industry_task = router.akshare.get_stock_industry_cn(stock_code)

        results = await asyncio.gather(
            info_task, financials_task, quote_task,
            fund_task, northbound_task, industry_task,
            return_exceptions=True,
        )

        keys = ["info", "financials", "quote", "fund_holdings", "northbound_holding", "sector_industry"]
        success_count = 0
        for i, key in enumerate(keys):
            if not isinstance(results[i], Exception):
                data[key] = results[i]
                success_count += 1
            else:
                logger.warning(f"Failed to get {key} for {symbol}: {results[i]}")

        logger.debug(f"Fundamental data preparation complete for {symbol}: {success_count}/{len(keys)} sources succeeded")
        return data
    else:
        # HK or other markets
        inst_task = router.yfinance.get_institutional_holders(symbol)
        sector_task = router.yfinance.get_sector_industry(symbol)

        results = await asyncio.gather(
            info_task, financials_task, quote_task, inst_task, sector_task,
            return_exceptions=True,
        )

        keys = ["info", "financials", "quote", "institutional_holders", "sector_industry"]
        success_count = 0
        for i, key in enumerate(keys):
            if not isinstance(results[i], Exception):
                data[key] = results[i]
                success_count += 1
            else:
                logger.warning(f"Failed to get {key} for {symbol}: {results[i]}")

        logger.debug(f"Fundamental data preparation complete for {symbol}: {success_count}/{len(keys)} sources succeeded")

    return data


def _build_fundamental_data_prompt(
    symbol: str,
    market: str,
    data: Dict[str, Any],
    language: str,
) -> str:
    """Build data section for fundamental analysis prompt."""
    sections = []

    # Quote section
    quote = data.get("quote")
    if quote:
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
    financials = data.get("financials")
    if financials:
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
    info = data.get("info")
    if info:
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

        # 2. Prepare data
        data = await _prepare_fundamental_data(symbol, market)

        # 3. Build prompt
        data_section = _build_fundamental_data_prompt(symbol, market, data, language)

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
            llm = await get_analysis_model_from_settings()
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


async def _prepare_technical_data(
    symbol: str,
    market: str,
) -> Dict[str, Any]:
    """Prepare data for technical analysis."""
    from app.services.stock_service import (
        HistoryInterval,
        HistoryPeriod,
        get_stock_service,
    )

    logger.debug(f"Preparing technical data for {symbol} ({market})")

    stock_service = await get_stock_service()

    quote_task = stock_service.get_quote(symbol)
    history_task = stock_service.get_history(
        symbol,
        period=HistoryPeriod.ONE_YEAR,
        interval=HistoryInterval.DAILY,
    )

    quote, history = await asyncio.gather(
        quote_task, history_task,
        return_exceptions=True,
    )

    quote_success = not isinstance(quote, Exception)
    history_success = not isinstance(history, Exception)

    if isinstance(quote, Exception):
        logger.warning(f"Failed to get quote for {symbol}: {quote}")
    if isinstance(history, Exception):
        logger.warning(f"Failed to get history for {symbol}: {history}")

    data = {
        "quote": quote if quote_success else None,
        "history": history if history_success else None,
        "indicators": {},
        "summary": {},
    }

    # Calculate indicators if we have history
    if data["history"] and data["history"].get("bars"):
        bars = data["history"]["bars"]
        data["indicators"] = _calculate_technical_indicators(bars)
        data["summary"] = _calculate_history_summary(bars)

    success_count = sum([quote_success, history_success])
    logger.debug(f"Technical data preparation complete for {symbol}: {success_count}/2 sources succeeded")

    return data


def _calculate_technical_indicators(bars: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculate technical indicators from price history."""
    if not bars or len(bars) < 20:
        return {}

    try:
        df = pd.DataFrame(bars)
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        df = df.sort_index()

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        indicators = {}

        # Moving Averages
        if len(df) >= 20:
            indicators["sma_20"] = float(df["close"].tail(20).mean())
        if len(df) >= 50:
            indicators["sma_50"] = float(df["close"].tail(50).mean())
        if len(df) >= 200:
            indicators["sma_200"] = float(df["close"].tail(200).mean())

        # RSI (14-period)
        if len(df) >= 15:
            delta = df["close"].diff()
            gain = delta.where(delta > 0, 0.0)
            loss = (-delta).where(delta < 0, 0.0)
            avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
            avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            indicators["rsi_14"] = float(rsi.iloc[-1])

        # MACD
        if len(df) >= 35:
            ema_12 = df["close"].ewm(span=12, adjust=False).mean()
            ema_26 = df["close"].ewm(span=26, adjust=False).mean()
            macd_line = ema_12 - ema_26
            signal_line = macd_line.ewm(span=9, adjust=False).mean()
            histogram = macd_line - signal_line

            indicators["macd"] = float(macd_line.iloc[-1])
            indicators["macd_signal"] = float(signal_line.iloc[-1])
            indicators["macd_histogram"] = float(histogram.iloc[-1])

        # Volume ratio
        if len(df) >= 20:
            avg_vol = df["volume"].tail(20).mean()
            current_vol = df["volume"].iloc[-1]
            if avg_vol > 0:
                indicators["volume_ratio"] = float(current_vol / avg_vol)

        # Volatility
        if len(df) >= 20:
            returns = df["close"].pct_change().tail(20)
            indicators["volatility_20d"] = float(returns.std() * 100 * (252 ** 0.5))

        return indicators

    except Exception as e:
        logger.error(f"Error calculating technical indicators: {e}")
        return {}


def _calculate_history_summary(bars: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculate price history summary."""
    if not bars:
        return {}

    try:
        df = pd.DataFrame(bars)
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        df = df.sort_index()

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        current_price = df["close"].iloc[-1]
        summary = {}

        # 52-week high/low
        year_data = df.tail(252) if len(df) >= 252 else df
        summary["high_52w"] = float(year_data["high"].max())
        summary["low_52w"] = float(year_data["low"].min())

        # Price changes
        if len(df) >= 5:
            summary["change_1w"] = ((current_price - df["close"].iloc[-5]) / df["close"].iloc[-5]) * 100
        if len(df) >= 22:
            summary["change_1m"] = ((current_price - df["close"].iloc[-22]) / df["close"].iloc[-22]) * 100
        if len(df) >= 66:
            summary["change_3m"] = ((current_price - df["close"].iloc[-66]) / df["close"].iloc[-66]) * 100

        return summary

    except Exception as e:
        logger.error(f"Error calculating history summary: {e}")
        return {}


def _build_technical_data_prompt(
    symbol: str,
    market: str,
    data: Dict[str, Any],
    language: str,
) -> str:
    """Build data section for technical analysis prompt."""
    sections = []

    # Quote
    quote = data.get("quote")
    if quote:
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
    indicators = data.get("indicators", {})
    if indicators:
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
    summary = data.get("summary", {})
    if summary:
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

        # 2. Prepare data
        data = await _prepare_technical_data(symbol, market)

        # 3. Build prompt
        data_section = _build_technical_data_prompt(symbol, market, data, language)

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
            llm = await get_analysis_model_from_settings()
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


async def _prepare_sentiment_data(
    symbol: str,
    market: str,
) -> Dict[str, Any]:
    """Prepare data for sentiment analysis."""
    from app.services.providers import get_provider_router
    from app.services.stock_service import (
        HistoryInterval,
        HistoryPeriod,
        get_stock_service,
    )

    logger.debug(f"Preparing sentiment data for {symbol} ({market})")

    stock_service = await get_stock_service()
    router = await get_provider_router()

    # Fetch data in parallel
    quote_task = stock_service.get_quote(symbol)
    history_task = stock_service.get_history(
        symbol,
        period=HistoryPeriod.THREE_MONTHS,
        interval=HistoryInterval.DAILY,
    )
    analyst_task = router.yfinance.get_analyst_ratings(symbol)

    quote, history, analyst_ratings = await asyncio.gather(
        quote_task, history_task, analyst_task,
        return_exceptions=True,
    )

    # Track data source results
    sources_status = {
        "quote": not isinstance(quote, Exception),
        "history": not isinstance(history, Exception),
        "analyst_ratings": not isinstance(analyst_ratings, Exception),
        "news": False,
        "market_context": False,
    }

    if isinstance(quote, Exception):
        logger.warning(f"Failed to get quote for sentiment: {quote}")
    if isinstance(history, Exception):
        logger.warning(f"Failed to get history for sentiment: {history}")
    if isinstance(analyst_ratings, Exception):
        logger.warning(f"Failed to get analyst ratings: {analyst_ratings}")

    # Try to get news
    news = None
    try:
        from app.services.news_service import get_news_service
        news_service = await get_news_service()
        articles = await news_service.get_news_by_symbol(symbol)
        if articles:
            news = articles[:10]
            sources_status["news"] = True
    except Exception as e:
        logger.debug(f"Failed to fetch news for sentiment: {e}")

    # Try to get market context
    market_context = None
    try:
        market_context = await router.get_market_context()
        sources_status["market_context"] = True
    except Exception as e:
        logger.debug(f"Failed to get market context: {e}")

    success_count = sum(sources_status.values())
    total_count = len(sources_status)
    logger.debug(f"Sentiment data preparation complete for {symbol}: {success_count}/{total_count} sources succeeded")

    return {
        "quote": quote if sources_status["quote"] else None,
        "history": history if sources_status["history"] else None,
        "analyst_ratings": analyst_ratings if sources_status["analyst_ratings"] else None,
        "news": news,
        "market_context": market_context,
    }


def _build_sentiment_data_prompt(
    symbol: str,
    market: str,
    data: Dict[str, Any],
    language: str,
) -> str:
    """Build data section for sentiment analysis prompt."""
    sections = []

    # Quote
    quote = data.get("quote")
    if quote:
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
    analyst = data.get("analyst_ratings")
    if analyst:
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
    news = data.get("news")
    if news:
        if language == "zh":
            sections.append("## 近期新闻")
            for article in news[:5]:
                sections.append(f"- [{article.get('source', '未知')}] {article.get('title', '')}")
        else:
            sections.append("## Recent News")
            for article in news[:5]:
                sections.append(f"- [{article.get('source', 'Unknown')}] {article.get('title', '')}")

    # Market context
    ctx = data.get("market_context")
    if ctx:
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

        # 2. Prepare data
        data = await _prepare_sentiment_data(symbol, market)

        # 3. Build prompt
        data_section = _build_sentiment_data_prompt(symbol, market, data, language)

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
            llm = await get_analysis_model_from_settings()
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


async def _prepare_news_data(
    symbol: str,
    market: str,
) -> Dict[str, Any]:
    """Prepare data for news analysis."""
    from app.services.stock_service import get_stock_service

    logger.debug(f"Preparing news data for {symbol} ({market})")

    # Track data source results
    news_success = False
    context_success = False

    # Fetch news
    articles = []
    try:
        from app.services.news_service import get_news_service
        news_service = await get_news_service()
        articles = await news_service.get_news_by_symbol(symbol)
        news_success = True
        logger.debug(f"Fetched {len(articles)} news articles for {symbol}")
    except Exception as e:
        logger.error(f"Failed to fetch news for {symbol}: {e}")

    # Get basic stock context
    stock_context = None
    try:
        stock_service = await get_stock_service()
        quote = await stock_service.get_quote(symbol)
        if quote:
            stock_context = {
                "price": quote.get("price"),
                "change_percent": quote.get("changePercent"),
                "market_cap": quote.get("marketCap"),
            }
            context_success = True
    except Exception as e:
        logger.debug(f"Could not get stock context for {symbol}: {e}")

    # Score and sort articles by importance
    scored_articles = _score_news_articles(articles)

    success_count = sum([news_success, context_success])
    logger.debug(f"News data preparation complete for {symbol}: {success_count}/2 sources succeeded")

    return {
        "articles": scored_articles,
        "stock_context": stock_context,
    }


def _score_news_articles(articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Score and sort news articles by importance."""
    SOURCE_WEIGHTS = {
        "reuters": 1.5, "bloomberg": 1.5, "wsj": 1.4, "cnbc": 1.3,
        "ft": 1.4, "barrons": 1.3, "seekingalpha": 1.1, "marketwatch": 1.2,
    }

    KEYWORDS = {
        "earnings": 2.0, "revenue": 1.8, "profit": 1.8, "acquisition": 2.0,
        "merger": 2.0, "bankruptcy": 2.5, "fraud": 2.5, "fda": 2.0,
        "approval": 1.8, "upgrade": 1.5, "downgrade": 1.5,
    }

    scored = []
    for article in articles:
        score = 1.0

        source = (article.get("source") or "").lower()
        for src_key, weight in SOURCE_WEIGHTS.items():
            if src_key in source:
                score *= weight
                break

        title = (article.get("title") or "").lower()
        summary = (article.get("summary") or "").lower()
        text = f"{title} {summary}"
        max_kw_weight = 1.0
        for kw, weight in KEYWORDS.items():
            if kw in text:
                max_kw_weight = max(max_kw_weight, weight)
        score *= max_kw_weight

        scored.append({**article, "_importance_score": round(score, 2)})

    scored.sort(key=lambda x: x.get("_importance_score", 0), reverse=True)
    return scored


def _build_news_data_prompt(
    symbol: str,
    market: str,
    data: Dict[str, Any],
    language: str,
) -> str:
    """Build data section for news analysis prompt."""
    articles = data.get("articles", [])

    if not articles:
        if language == "zh":
            return f"未找到 {symbol} 的近期新闻。请提供一般性市场展望。"
        return f"No recent news found for {symbol}. Please provide a general market outlook."

    sections = []

    # Stock context
    ctx = data.get("stock_context")
    if ctx:
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

        # 2. Prepare data
        data = await _prepare_news_data(symbol, market)

        # 3. Build prompt
        data_section = _build_news_data_prompt(symbol, market, data, language)

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
            llm = await get_analysis_model_from_settings()
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
