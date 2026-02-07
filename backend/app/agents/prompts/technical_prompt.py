"""Prompt templates for technical analysis agent."""

from typing import Any, Dict, List, Optional

from app.agents.prompts.sanitizer import (
    sanitize_dict_values,
    sanitize_market,
    sanitize_symbol,
)

# Market-specific context templates (English)
MARKET_CONTEXT_EN = {
    "us": """
You are analyzing a US-listed stock. Consider:
- US market trading hours (9:30 AM - 4:00 PM ET)
- Pre-market and after-hours trading impact
- Dollar volume and institutional activity
- Correlation with major indices (S&P 500, NASDAQ)
""",
    "hk": """
You are analyzing a Hong Kong-listed stock. Consider:
- HK market trading hours (9:30 AM - 4:00 PM HKT)
- Lunch break (12:00 PM - 1:00 PM)
- Impact of mainland China news
- Dual-listed stocks (A-H premium/discount)
- Hang Seng Index correlation
""",
    "sh": """
You are analyzing a Shanghai A-share stock. Consider:
- Shanghai market hours (9:30 AM - 3:00 PM CST)
- Lunch break (11:30 AM - 1:00 PM)
- Daily price limits (+/- 10%, +/- 20% for STAR Market)
- Northbound (Stock Connect) flows
- State media and policy announcements
""",
    "sz": """
You are analyzing a Shenzhen A-share stock. Consider:
- Shenzhen market hours (9:30 AM - 3:00 PM CST)
- Lunch break (11:30 AM - 1:00 PM)
- Daily price limits (+/- 10%, +/- 20% for ChiNext)
- Northbound (Stock Connect) flows
- Higher retail participation
""",
}

# Market-specific context templates (Chinese)
MARKET_CONTEXT_ZH = {
    "us": """
你正在分析一只美股。请注意：
- 美国市场交易时间（美东时间 9:30 AM - 4:00 PM）
- 盘前和盘后交易影响
- 美元成交量和机构活动
- 与主要指数的相关性（标普500、纳斯达克）
""",
    "hk": """
你正在分析一只港股。请注意：
- 香港市场交易时间（9:30 AM - 4:00 PM HKT）
- 午间休市（12:00 PM - 1:00 PM）
- 中国内地消息的影响
- 双重上市股票（A-H股溢价/折价）
- 与恒生指数的相关性
""",
    "sh": """
你正在分析一只上海A股。请注意：
- 上海市场交易时间（9:30 AM - 3:00 PM 北京时间）
- 午间休市（11:30 AM - 1:00 PM）
- 每日涨跌幅限制（+/- 10%，科创板 +/- 20%）
- 北向资金（沪港通）流向
- 官方媒体和政策公告
""",
    "sz": """
你正在分析一只深圳A股。请注意：
- 深圳市场交易时间（9:30 AM - 3:00 PM 北京时间）
- 午间休市（11:30 AM - 1:00 PM）
- 每日涨跌幅限制（+/- 10%，创业板 +/- 20%）
- 北向资金（深港通）流向
- 较高的散户参与度
""",
}

# Backward compatibility
MARKET_CONTEXT = MARKET_CONTEXT_EN

TECHNICAL_SYSTEM_PROMPT_EN = """You are an expert technical analyst providing detailed price action analysis.

{market_context}

Your analysis should be:
1. Based on price patterns and indicators
2. Clear about timeframes (short/medium/long term)
3. Specific about key price levels
4. Honest about limitations of technical analysis

Write your analysis in well-formatted Markdown. Use the following structure:

## Summary
A concise 2-3 sentence overview of the technical outlook.

## Trend Analysis
Describe short-term, medium-term, and long-term trends with key observations.

## Key Levels
### Support
- List support levels with prices

### Resistance
- List resistance levels with prices

## Indicator Analysis
Analyze moving averages (crossovers, positions), RSI, MACD, and volume. Use bullet points.

## Chart Patterns
Describe any identified chart patterns.

## Signals
### Bullish Signals
- List bullish signals

### Bearish Signals
- List bearish signals

## Recommendation
State your bias (Bullish / Neutral / Bearish), suggested entry zone, stop loss, price targets, and rationale.

After your Markdown analysis, include a structured data block for machine parsing:

```json
{{
    "trend_short": "bullish|neutral|bearish",
    "trend_medium": "bullish|neutral|bearish",
    "trend_long": "bullish|neutral|bearish",
    "bias": "bullish|neutral|bearish",
    "support": ["price1", "price2"],
    "resistance": ["price1", "price2"],
    "bullish_signals": ["signal1", "signal2"],
    "bearish_signals": ["signal1", "signal2"]
}}
```
"""

TECHNICAL_SYSTEM_PROMPT_ZH = """你是一位专业的技术分析师，提供详细的价格走势分析。

{market_context}

你的分析应当：
1. 基于价格形态和技术指标
2. 清晰地区分时间维度（短期/中期/长期）
3. 明确关键价格位置
4. 诚实地说明技术分析的局限性

请使用格式良好的 Markdown 撰写分析报告，采用以下结构：

## 摘要
用2-3句话简要概述技术面展望。

## 趋势分析
描述短期、中期和长期趋势及关键观察。

## 关键价位
### 支撑位
- 列出支撑价位及价格

### 阻力位
- 列出阻力价位及价格

## 指标分析
分析均线（交叉、位置）、RSI、MACD 和成交量。使用要点列表。

## 图表形态
描述识别到的图表形态。

## 信号
### 多头信号
- 列出看涨信号

### 空头信号
- 列出看跌信号

## 操作建议
给出你的偏向（看多 / 中性 / 看空），建议入场区间，止损位，目标价位及理由。

在 Markdown 分析之后，请附上一个用于机器解析的结构化数据块：

```json
{{
    "trend_short": "bullish|neutral|bearish",
    "trend_medium": "bullish|neutral|bearish",
    "trend_long": "bullish|neutral|bearish",
    "bias": "bullish|neutral|bearish",
    "support": ["价位1", "价位2"],
    "resistance": ["价位1", "价位2"],
    "bullish_signals": ["信号1", "信号2"],
    "bearish_signals": ["信号1", "信号2"]
}}
```
"""

# Backward compatibility
TECHNICAL_SYSTEM_PROMPT = TECHNICAL_SYSTEM_PROMPT_EN


def build_technical_prompt(
    symbol: str,
    market: str,
    quote: Optional[Dict[str, Any]],
    indicators: Optional[Dict[str, Any]],
    history_summary: Optional[Dict[str, Any]],
    language: str = "en",
) -> str:
    """
    Build the technical analysis prompt with price data and indicators.

    Args:
        symbol: Stock symbol
        market: Market identifier (us, hk, sh, sz)
        quote: Current quote data
        indicators: Calculated technical indicators
        history_summary: Summary of price history
        language: Output language ('en' or 'zh')

    Returns:
        Formatted prompt string
    """
    # Sanitize inputs to prevent prompt injection
    symbol = sanitize_symbol(symbol)
    market = sanitize_market(market)

    # Sanitize data dictionaries
    quote = sanitize_dict_values(quote) if quote else None
    indicators = sanitize_dict_values(indicators) if indicators else None
    history_summary = sanitize_dict_values(history_summary) if history_summary else None

    # Build data sections
    data_sections = []

    # Current Price Section
    if quote:
        if language == "zh":
            quote_text = f"""
## 当前市场数据
- **当前价格**: ${quote.get('price', '暂无')}
- **涨跌幅**: {quote.get('change', '暂无')} ({quote.get('change_percent', '暂无')}%)
- **日内最高**: ${quote.get('day_high', '暂无')}
- **日内最低**: ${quote.get('day_low', '暂无')}
- **开盘价**: ${quote.get('open', '暂无')}
- **昨收价**: ${quote.get('previous_close', '暂无')}
- **成交量**: {_format_volume(quote.get('volume'))}
"""
        else:
            quote_text = f"""
## Current Market Data
- **Current Price**: ${quote.get('price', 'N/A')}
- **Change**: {quote.get('change', 'N/A')} ({quote.get('change_percent', 'N/A')}%)
- **Day High**: ${quote.get('day_high', 'N/A')}
- **Day Low**: ${quote.get('day_low', 'N/A')}
- **Open**: ${quote.get('open', 'N/A')}
- **Previous Close**: ${quote.get('previous_close', 'N/A')}
- **Volume**: {_format_volume(quote.get('volume'))}
"""
        data_sections.append(quote_text)

    # Price History Summary
    if history_summary:
        if language == "zh":
            history_text = f"""
## 价格历史摘要
- **52周最高**: ${history_summary.get('high_52w', '暂无')}
- **52周最低**: ${history_summary.get('low_52w', '暂无')}
- **20日平均成交量**: {_format_volume(history_summary.get('avg_volume_20d'))}
- **1个月涨跌幅**: {_format_percent(history_summary.get('change_1m'))}
- **3个月涨跌幅**: {_format_percent(history_summary.get('change_3m'))}
- **1年涨跌幅**: {_format_percent(history_summary.get('change_1y'))}
- **20日波动率**: {_format_percent(history_summary.get('volatility_20d'))}
"""
        else:
            history_text = f"""
## Price History Summary
- **52-Week High**: ${history_summary.get('high_52w', 'N/A')}
- **52-Week Low**: ${history_summary.get('low_52w', 'N/A')}
- **Average Volume (20d)**: {_format_volume(history_summary.get('avg_volume_20d'))}
- **Price Change (1 Month)**: {_format_percent(history_summary.get('change_1m'))}
- **Price Change (3 Months)**: {_format_percent(history_summary.get('change_3m'))}
- **Price Change (1 Year)**: {_format_percent(history_summary.get('change_1y'))}
- **Volatility (20d)**: {_format_percent(history_summary.get('volatility_20d'))}
"""
        data_sections.append(history_text)

    # Technical Indicators Section
    if indicators:
        if language == "zh":
            indicators_text = f"""
## 技术指标

### 移动平均线
- **SMA 20**: ${_format_price(indicators.get('sma_20'))}
- **SMA 50**: ${_format_price(indicators.get('sma_50'))}
- **SMA 200**: ${_format_price(indicators.get('sma_200'))}
- **EMA 12**: ${_format_price(indicators.get('ema_12'))}
- **EMA 26**: ${_format_price(indicators.get('ema_26'))}

### 价格相对均线位置
- **vs SMA 20**: {_format_vs_ma(quote.get('price') if quote else None, indicators.get('sma_20'), language)}
- **vs SMA 50**: {_format_vs_ma(quote.get('price') if quote else None, indicators.get('sma_50'), language)}
- **vs SMA 200**: {_format_vs_ma(quote.get('price') if quote else None, indicators.get('sma_200'), language)}

### 动量指标
- **RSI (14)**: {_format_indicator(indicators.get('rsi_14'))} {_interpret_rsi(indicators.get('rsi_14'), language)}
- **MACD**: {_format_indicator(indicators.get('macd'))}
- **MACD 信号线**: {_format_indicator(indicators.get('macd_signal'))}
- **MACD 柱状图**: {_format_indicator(indicators.get('macd_hist'))} {_interpret_macd(indicators.get('macd_hist'), language)}

### 波动率与趋势
- **ATR (14)**: ${_format_price(indicators.get('atr_14'))}
- **布林带上轨**: ${_format_price(indicators.get('bb_upper'))}
- **布林带中轨**: ${_format_price(indicators.get('bb_middle'))}
- **布林带下轨**: ${_format_price(indicators.get('bb_lower'))}

### 成交量指标
- **成交量 vs 20日均量**: {_format_volume_ratio(indicators.get('volume_ratio'), language)}
- **OBV 趋势**: {indicators.get('obv_trend', '暂无')}
"""
        else:
            indicators_text = f"""
## Technical Indicators

### Moving Averages
- **SMA 20**: ${_format_price(indicators.get('sma_20'))}
- **SMA 50**: ${_format_price(indicators.get('sma_50'))}
- **SMA 200**: ${_format_price(indicators.get('sma_200'))}
- **EMA 12**: ${_format_price(indicators.get('ema_12'))}
- **EMA 26**: ${_format_price(indicators.get('ema_26'))}

### Price vs Moving Averages
- **vs SMA 20**: {_format_vs_ma(quote.get('price') if quote else None, indicators.get('sma_20'), language)}
- **vs SMA 50**: {_format_vs_ma(quote.get('price') if quote else None, indicators.get('sma_50'), language)}
- **vs SMA 200**: {_format_vs_ma(quote.get('price') if quote else None, indicators.get('sma_200'), language)}

### Momentum Indicators
- **RSI (14)**: {_format_indicator(indicators.get('rsi_14'))} {_interpret_rsi(indicators.get('rsi_14'), language)}
- **MACD**: {_format_indicator(indicators.get('macd'))}
- **MACD Signal**: {_format_indicator(indicators.get('macd_signal'))}
- **MACD Histogram**: {_format_indicator(indicators.get('macd_hist'))} {_interpret_macd(indicators.get('macd_hist'), language)}

### Volatility & Trend
- **ATR (14)**: ${_format_price(indicators.get('atr_14'))}
- **Bollinger Upper**: ${_format_price(indicators.get('bb_upper'))}
- **Bollinger Middle**: ${_format_price(indicators.get('bb_middle'))}
- **Bollinger Lower**: ${_format_price(indicators.get('bb_lower'))}

### Volume Indicators
- **Volume vs 20d Avg**: {_format_volume_ratio(indicators.get('volume_ratio'), language)}
- **OBV Trend**: {indicators.get('obv_trend', 'N/A')}
"""
        data_sections.append(indicators_text)

    # Recent Price Action
    if history_summary and history_summary.get('recent_prices'):
        recent = history_summary['recent_prices']
        if language == "zh":
            recent_text = """
## 近期价格走势（最近5日）
| 日期 | 开盘 | 最高 | 最低 | 收盘 | 成交量 |
|------|------|------|-----|-------|--------|
"""
        else:
            recent_text = """
## Recent Price Action (Last 5 Days)
| Date | Open | High | Low | Close | Volume |
|------|------|------|-----|-------|--------|
"""
        for bar in recent[-5:]:
            recent_text += f"| {bar.get('date', 'N/A')} | ${bar.get('open', 'N/A')} | ${bar.get('high', 'N/A')} | ${bar.get('low', 'N/A')} | ${bar.get('close', 'N/A')} | {_format_volume(bar.get('volume'))} |\n"
        data_sections.append(recent_text)

    # Combine all sections
    data_content = "\n".join(data_sections) if data_sections else ("数据有限。" if language == "zh" else "Limited data available.")

    if language == "zh":
        user_prompt = f"""
# 技术分析请求

**股票代码**: {symbol}
**市场**: {market.upper()}

{data_content}

---

请根据以上数据，对 {symbol} 进行全面的技术分析。
重点关注趋势判断、关键支撑/阻力位、指标信号和可操作建议。
如果数据缺失或不完整，请在分析中注明，并基于现有数据进行分析。
"""
    else:
        user_prompt = f"""
# Technical Analysis Request

**Stock Symbol**: {symbol}
**Market**: {market.upper()}

{data_content}

---

Please provide a comprehensive technical analysis of {symbol} based on the above data.
Focus on trend identification, key support/resistance levels, indicator signals, and actionable insights.
If data is missing or incomplete, note this in your analysis and work with what's available.
"""

    return user_prompt


def get_system_prompt(market: str, language: str = "en") -> str:
    """
    Get the system prompt with market-specific context.

    Args:
        market: Market identifier (us, hk, sh, sz)
        language: Output language ('en' or 'zh')

    Returns:
        System prompt string
    """
    if language == "zh":
        market_contexts = MARKET_CONTEXT_ZH
        system_prompt = TECHNICAL_SYSTEM_PROMPT_ZH
    else:
        market_contexts = MARKET_CONTEXT_EN
        system_prompt = TECHNICAL_SYSTEM_PROMPT_EN

    market_context = market_contexts.get(market.lower(), market_contexts["us"])
    return system_prompt.format(market_context=market_context)


def _format_price(value: Optional[float]) -> str:
    """Format a price for display."""
    if value is None:
        return "N/A"
    return f"{value:.2f}"


def _format_volume(value: Optional[int]) -> str:
    """Format volume for display."""
    if value is None:
        return "N/A"
    if value >= 1e9:
        return f"{value / 1e9:.2f}B"
    if value >= 1e6:
        return f"{value / 1e6:.2f}M"
    if value >= 1e3:
        return f"{value / 1e3:.2f}K"
    return str(value)


def _format_percent(value: Optional[float]) -> str:
    """Format a percentage for display."""
    if value is None:
        return "N/A"
    return f"{value:+.2f}%"


def _format_indicator(value: Optional[float]) -> str:
    """Format an indicator value for display."""
    if value is None:
        return "N/A"
    return f"{value:.2f}"


def _format_vs_ma(price: Optional[float], ma: Optional[float], language: str = "en") -> str:
    """Format price vs moving average comparison."""
    if price is None or ma is None:
        return "N/A"
    diff = ((price - ma) / ma) * 100
    if language == "zh":
        status = "上方" if diff > 0 else "下方"
    else:
        status = "above" if diff > 0 else "below"
    return f"{abs(diff):.2f}% {status}"


def _format_volume_ratio(ratio: Optional[float], language: str = "en") -> str:
    """Format volume ratio for display."""
    if ratio is None:
        return "N/A"
    if language == "zh":
        if ratio > 1.5:
            return f"{ratio:.2f}x (放量)"
        if ratio < 0.5:
            return f"{ratio:.2f}x (缩量)"
        return f"{ratio:.2f}x (正常)"
    else:
        if ratio > 1.5:
            return f"{ratio:.2f}x (High)"
        if ratio < 0.5:
            return f"{ratio:.2f}x (Low)"
        return f"{ratio:.2f}x (Normal)"


def _interpret_rsi(rsi: Optional[float], language: str = "en") -> str:
    """Interpret RSI value."""
    if rsi is None:
        return ""
    if language == "zh":
        if rsi >= 70:
            return "(超买)"
        if rsi <= 30:
            return "(超卖)"
        if rsi >= 60:
            return "(偏多)"
        if rsi <= 40:
            return "(偏空)"
        return "(中性)"
    else:
        if rsi >= 70:
            return "(Overbought)"
        if rsi <= 30:
            return "(Oversold)"
        if rsi >= 60:
            return "(Bullish)"
        if rsi <= 40:
            return "(Bearish)"
        return "(Neutral)"


def _interpret_macd(hist: Optional[float], language: str = "en") -> str:
    """Interpret MACD histogram."""
    if hist is None:
        return ""
    if language == "zh":
        if hist > 0:
            return "(多头动能)"
        return "(空头动能)"
    else:
        if hist > 0:
            return "(Bullish momentum)"
        return "(Bearish momentum)"
