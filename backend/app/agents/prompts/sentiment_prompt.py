"""Prompt templates for sentiment analysis agent."""

from typing import Any, Dict, List, Optional

from app.agents.prompts.sanitizer import (
    sanitize_dict_values,
    sanitize_market,
    sanitize_news_article,
    sanitize_symbol,
)

# Market-specific context templates
MARKET_CONTEXT = {
    "us": """
You are analyzing sentiment for a US-listed stock. Consider:
- Impact of US economic data (Fed decisions, employment, inflation)
- Earnings season dynamics
- Analyst ratings and price targets
- Social media and retail investor sentiment
- Institutional ownership changes (13F filings)
- Sector rotation patterns
""",
    "hk": """
You are analyzing sentiment for a Hong Kong-listed stock. Consider:
- China policy announcements impact
- US-China relations
- Mainland investor sentiment (Southbound flows)
- Hong Kong political and economic developments
- Cross-listing dynamics (ADR/H-share)
- Regional fund flows
""",
    "sh": """
You are analyzing sentiment for a Shanghai A-share stock. Consider:
- Government policy and regulatory changes
- State media commentary
- Northbound (foreign) investor flows
- Retail investor behavior (high retail participation)
- Margin trading and short selling data
- Industry policy support or restrictions
""",
    "sz": """
You are analyzing sentiment for a Shenzhen A-share stock. Consider:
- Technology and innovation policy support
- ChiNext/STAR Market specific sentiment
- Retail investor speculation patterns
- Northbound investor flows
- IPO and secondary offering activity
- Cross-holdings and related party dynamics
""",
}

SENTIMENT_SYSTEM_PROMPT = """You are an expert market sentiment analyst providing comprehensive sentiment assessment.

{market_context}

Your analysis should consider:
1. Price momentum as a sentiment indicator
2. Volume patterns indicating conviction
3. Market context and sector trends
4. News impact (when available)
5. Technical sentiment indicators

Write your analysis in well-formatted Markdown. Use the following structure:

## Summary
A concise 2-3 sentence overview of the overall sentiment.

## Overall Sentiment
State the sentiment (Very Bearish / Bearish / Neutral / Bullish / Very Bullish) with a score from -100 to 100 and confidence level.

## Price Momentum
Assess recent price action: strong downtrend, downtrend, neutral, uptrend, or strong uptrend. Explain your reasoning.

## Volume Sentiment
Assess volume patterns: distribution, neutral, or accumulation. Explain your reasoning.

## Market Context
Discuss sector trends, broader market sentiment, and how this stock compares to peers/market.

## News Sentiment
If news is available, analyze key themes and their impact. If no news is available, note this.

## Risk Factors
- List sentiment-related risks to monitor

## Catalysts
### Bullish Catalysts
- List potential positive catalysts

### Bearish Catalysts
- List potential negative catalysts

## Recommendation
State your sentiment bias (Bullish / Neutral / Bearish), timing considerations, and rationale.

After your Markdown analysis, include a structured data block for machine parsing:

```json
{{
    "sentiment_score": <number from -100 to 100>,
    "sentiment_label": "very_bearish|bearish|neutral|bullish|very_bullish",
    "confidence": "low|medium|high",
    "momentum": "strong_downtrend|downtrend|neutral|uptrend|strong_uptrend",
    "volume": "distribution|neutral|accumulation",
    "sentiment_bias": "bullish|neutral|bearish"
}}
```
"""


def build_sentiment_prompt(
    symbol: str,
    market: str,
    quote: Optional[Dict[str, Any]],
    history_summary: Optional[Dict[str, Any]],
    news: Optional[List[Dict[str, Any]]],
    market_context: Optional[Dict[str, Any]],
) -> str:
    """
    Build the sentiment analysis prompt with market data.

    Args:
        symbol: Stock symbol
        market: Market identifier (us, hk, sh, sz)
        quote: Current quote data
        history_summary: Summary of price history
        news: Recent news articles (if available)
        market_context: Broader market context data

    Returns:
        Formatted prompt string
    """
    # Sanitize inputs to prevent prompt injection
    symbol = sanitize_symbol(symbol)
    market = sanitize_market(market)

    # Sanitize data dictionaries
    quote = sanitize_dict_values(quote) if quote else None
    history_summary = sanitize_dict_values(history_summary) if history_summary else None
    market_context = sanitize_dict_values(market_context) if market_context else None

    # Sanitize news articles (user-provided content is especially risky)
    if news:
        news = [sanitize_news_article(article) for article in news]

    # Build data sections
    data_sections = []

    # Current Price & Momentum Section
    if quote:
        quote_text = f"""
## Current Market Data
- **Current Price**: ${quote.get('price', 'N/A')}
- **Daily Change**: {quote.get('change', 'N/A')} ({quote.get('change_percent', 'N/A')}%)
- **Day Range**: ${quote.get('day_low', 'N/A')} - ${quote.get('day_high', 'N/A')}
- **Volume**: {_format_volume(quote.get('volume'))}
- **Previous Close**: ${quote.get('previous_close', 'N/A')}
"""
        data_sections.append(quote_text)

    # Price Momentum Section
    if history_summary:
        momentum_text = f"""
## Price Momentum Analysis
### Performance
- **1-Day Change**: {_format_percent(history_summary.get('change_1d'))}
- **1-Week Change**: {_format_percent(history_summary.get('change_1w'))}
- **1-Month Change**: {_format_percent(history_summary.get('change_1m'))}
- **3-Month Change**: {_format_percent(history_summary.get('change_3m'))}
- **YTD Change**: {_format_percent(history_summary.get('change_ytd'))}
- **1-Year Change**: {_format_percent(history_summary.get('change_1y'))}

### Trend Indicators
- **52-Week High**: ${history_summary.get('high_52w', 'N/A')}
- **52-Week Low**: ${history_summary.get('low_52w', 'N/A')}
- **Distance from 52W High**: {_format_percent(history_summary.get('pct_from_high'))}
- **Distance from 52W Low**: {_format_percent(history_summary.get('pct_from_low'))}

### Volume Analysis
- **Average Volume (20d)**: {_format_volume(history_summary.get('avg_volume_20d'))}
- **Volume vs Average**: {_format_volume_ratio(history_summary.get('volume_ratio'))}
- **Volume Trend**: {history_summary.get('volume_trend', 'N/A')}

### Volatility
- **20-Day Volatility**: {_format_percent(history_summary.get('volatility_20d'))}
- **Volatility Rank**: {history_summary.get('volatility_rank', 'N/A')}
"""
        data_sections.append(momentum_text)

    # News Section
    if news and len(news) > 0:
        news_text = """
## Recent News
"""
        for i, article in enumerate(news[:5], 1):
            news_text += f"""
### {i}. {article.get('title', 'Untitled')}
- **Source**: {article.get('source', 'Unknown')}
- **Published**: {article.get('published_at', 'N/A')}
- **Summary**: {article.get('summary', 'No summary available.')[:200]}
"""
        data_sections.append(news_text)
    else:
        data_sections.append("""
## News
No recent news available for analysis. Sentiment will be assessed primarily based on price action and market context.
""")

    # Market Context Section
    if market_context:
        context_text = f"""
## Market Context
### Broader Market
- **Market Index Change**: {_format_percent(market_context.get('index_change'))}
- **Market Trend**: {market_context.get('market_trend', 'N/A')}
- **Sector Performance**: {_format_percent(market_context.get('sector_change'))}
- **Sector Trend**: {market_context.get('sector_trend', 'N/A')}

### Relative Performance
- **vs Market**: {_format_percent(market_context.get('vs_market'))}
- **vs Sector**: {_format_percent(market_context.get('vs_sector'))}
- **Relative Strength**: {market_context.get('relative_strength', 'N/A')}
"""
        data_sections.append(context_text)

    # Combine all sections
    data_content = "\n".join(data_sections) if data_sections else "Limited data available."

    user_prompt = f"""
# Sentiment Analysis Request

**Stock Symbol**: {symbol}
**Market**: {market.upper()}

{data_content}

---

Please provide a comprehensive sentiment analysis of {symbol} based on the above data.
Focus on price momentum, volume patterns, news impact (if available), and overall market sentiment.
Assess the conviction behind recent price moves and identify potential sentiment shifts.
If data is missing or incomplete, note this in your analysis and work with what's available.
"""

    return user_prompt


def get_system_prompt(market: str) -> str:
    """Get the system prompt with market-specific context."""
    market_context = MARKET_CONTEXT.get(market.lower(), MARKET_CONTEXT["us"])
    return SENTIMENT_SYSTEM_PROMPT.format(market_context=market_context)


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


def _format_volume_ratio(ratio: Optional[float]) -> str:
    """Format volume ratio for display."""
    if ratio is None:
        return "N/A"
    if ratio > 2.0:
        return f"{ratio:.2f}x (Very High)"
    if ratio > 1.5:
        return f"{ratio:.2f}x (High)"
    if ratio < 0.5:
        return f"{ratio:.2f}x (Low)"
    return f"{ratio:.2f}x (Normal)"
