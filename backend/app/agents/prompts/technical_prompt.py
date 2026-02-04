"""Prompt templates for technical analysis agent."""

from typing import Any, Dict, List, Optional

from app.agents.prompts.sanitizer import (
    sanitize_dict_values,
    sanitize_market,
    sanitize_symbol,
)

# Market-specific context templates
MARKET_CONTEXT = {
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

TECHNICAL_SYSTEM_PROMPT = """You are an expert technical analyst providing detailed price action analysis.

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


def build_technical_prompt(
    symbol: str,
    market: str,
    quote: Optional[Dict[str, Any]],
    indicators: Optional[Dict[str, Any]],
    history_summary: Optional[Dict[str, Any]],
) -> str:
    """
    Build the technical analysis prompt with price data and indicators.

    Args:
        symbol: Stock symbol
        market: Market identifier (us, hk, sh, sz)
        quote: Current quote data
        indicators: Calculated technical indicators
        history_summary: Summary of price history

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
        indicators_text = f"""
## Technical Indicators

### Moving Averages
- **SMA 20**: ${_format_price(indicators.get('sma_20'))}
- **SMA 50**: ${_format_price(indicators.get('sma_50'))}
- **SMA 200**: ${_format_price(indicators.get('sma_200'))}
- **EMA 12**: ${_format_price(indicators.get('ema_12'))}
- **EMA 26**: ${_format_price(indicators.get('ema_26'))}

### Price vs Moving Averages
- **vs SMA 20**: {_format_vs_ma(quote.get('price'), indicators.get('sma_20'))}
- **vs SMA 50**: {_format_vs_ma(quote.get('price'), indicators.get('sma_50'))}
- **vs SMA 200**: {_format_vs_ma(quote.get('price'), indicators.get('sma_200'))}

### Momentum Indicators
- **RSI (14)**: {_format_indicator(indicators.get('rsi_14'))} {_interpret_rsi(indicators.get('rsi_14'))}
- **MACD**: {_format_indicator(indicators.get('macd'))}
- **MACD Signal**: {_format_indicator(indicators.get('macd_signal'))}
- **MACD Histogram**: {_format_indicator(indicators.get('macd_hist'))} {_interpret_macd(indicators.get('macd_hist'))}

### Volatility & Trend
- **ATR (14)**: ${_format_price(indicators.get('atr_14'))}
- **Bollinger Upper**: ${_format_price(indicators.get('bb_upper'))}
- **Bollinger Middle**: ${_format_price(indicators.get('bb_middle'))}
- **Bollinger Lower**: ${_format_price(indicators.get('bb_lower'))}

### Volume Indicators
- **Volume vs 20d Avg**: {_format_volume_ratio(indicators.get('volume_ratio'))}
- **OBV Trend**: {indicators.get('obv_trend', 'N/A')}
"""
        data_sections.append(indicators_text)

    # Recent Price Action
    if history_summary and history_summary.get('recent_prices'):
        recent = history_summary['recent_prices']
        recent_text = """
## Recent Price Action (Last 5 Days)
| Date | Open | High | Low | Close | Volume |
|------|------|------|-----|-------|--------|
"""
        for bar in recent[-5:]:
            recent_text += f"| {bar.get('date', 'N/A')} | ${bar.get('open', 'N/A')} | ${bar.get('high', 'N/A')} | ${bar.get('low', 'N/A')} | ${bar.get('close', 'N/A')} | {_format_volume(bar.get('volume'))} |\n"
        data_sections.append(recent_text)

    # Combine all sections
    data_content = "\n".join(data_sections) if data_sections else "Limited data available."

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


def get_system_prompt(market: str) -> str:
    """Get the system prompt with market-specific context."""
    market_context = MARKET_CONTEXT.get(market.lower(), MARKET_CONTEXT["us"])
    return TECHNICAL_SYSTEM_PROMPT.format(market_context=market_context)


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


def _format_vs_ma(price: Optional[float], ma: Optional[float]) -> str:
    """Format price vs moving average comparison."""
    if price is None or ma is None:
        return "N/A"
    diff = ((price - ma) / ma) * 100
    status = "above" if diff > 0 else "below"
    return f"{abs(diff):.2f}% {status}"


def _format_volume_ratio(ratio: Optional[float]) -> str:
    """Format volume ratio for display."""
    if ratio is None:
        return "N/A"
    if ratio > 1.5:
        return f"{ratio:.2f}x (High)"
    if ratio < 0.5:
        return f"{ratio:.2f}x (Low)"
    return f"{ratio:.2f}x (Normal)"


def _interpret_rsi(rsi: Optional[float]) -> str:
    """Interpret RSI value."""
    if rsi is None:
        return ""
    if rsi >= 70:
        return "(Overbought)"
    if rsi <= 30:
        return "(Oversold)"
    if rsi >= 60:
        return "(Bullish)"
    if rsi <= 40:
        return "(Bearish)"
    return "(Neutral)"


def _interpret_macd(hist: Optional[float]) -> str:
    """Interpret MACD histogram."""
    if hist is None:
        return ""
    if hist > 0:
        return "(Bullish momentum)"
    return "(Bearish momentum)"
