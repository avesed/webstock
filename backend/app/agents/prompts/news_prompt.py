"""Prompt templates for news analysis."""

from typing import Any, Dict, Optional

from app.agents.prompts.sanitizer import sanitize_dict_values, sanitize_symbol


NEWS_ANALYSIS_SYSTEM_PROMPT = """You are an expert financial news analyst specializing in assessing news impact on stock prices.

Your analysis should be:
1. Objective and balanced
2. Focused on market-relevant information
3. Clear about uncertainty when information is limited
4. Mindful of both short-term and long-term implications

Always structure your response in the following JSON format:
{
    "sentiment_score": <number from -1.0 to 1.0>,
    "sentiment_label": "positive" | "negative" | "neutral",
    "impact_prediction": {
        "direction": "bullish" | "bearish" | "neutral",
        "magnitude": "high" | "medium" | "low",
        "timeframe": "immediate" | "short_term" | "long_term",
        "confidence": "high" | "medium" | "low"
    },
    "key_points": [
        "Key point 1",
        "Key point 2",
        "Key point 3"
    ],
    "summary": "A brief 2-3 sentence summary of the news and its implications",
    "risk_factors": [
        "Risk or caveat 1",
        "Risk or caveat 2"
    ],
    "related_themes": [
        "Theme 1 (e.g., earnings, regulation, competition)",
        "Theme 2"
    ]
}

Sentiment Score Guidelines:
- 1.0: Extremely positive (major positive catalyst, M&A at premium, breakthrough)
- 0.5 to 0.9: Positive (earnings beat, new product, expansion)
- 0.1 to 0.4: Slightly positive (minor good news, reaffirmed guidance)
- -0.1 to 0.1: Neutral (routine news, mixed signals)
- -0.4 to -0.1: Slightly negative (minor concerns, guidance in-line)
- -0.9 to -0.5: Negative (earnings miss, regulatory issues)
- -1.0: Extremely negative (major scandal, bankruptcy risk, fraud)
"""


def build_news_analysis_prompt(
    symbol: str,
    title: str,
    summary: Optional[str],
    source: str,
    published_at: str,
    market: str,
    additional_context: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Build the news analysis prompt.

    Args:
        symbol: Stock symbol
        title: News article title
        summary: News article summary/content
        source: News source
        published_at: Publication timestamp
        market: Market identifier (US, HK, SH, SZ)
        additional_context: Optional additional context (stock price, etc.)

    Returns:
        Formatted prompt string
    """
    # Sanitize inputs
    symbol = sanitize_symbol(symbol)
    title = _sanitize_text(title, max_length=500)
    summary = _sanitize_text(summary, max_length=2000) if summary else "No summary available."
    source = _sanitize_text(source, max_length=100)

    if additional_context:
        additional_context = sanitize_dict_values(additional_context)

    # Build market context
    market_context = _get_market_context(market)

    # Build additional context section
    context_section = ""
    if additional_context:
        context_section = f"""
## Current Stock Context
- **Current Price**: ${additional_context.get('price', 'N/A')}
- **Daily Change**: {additional_context.get('change_percent', 'N/A')}%
- **Market Cap**: {additional_context.get('market_cap', 'N/A')}
- **Sector**: {additional_context.get('sector', 'N/A')}
"""

    prompt = f"""
# News Analysis Request

## Stock Information
- **Symbol**: {symbol}
- **Market**: {market}
{market_context}

## News Article
- **Source**: {source}
- **Published**: {published_at}

### Title
{title}

### Content/Summary
{summary}
{context_section}

---

Please analyze this news article and provide:
1. A sentiment score (-1.0 to 1.0)
2. Impact prediction on the stock price
3. Key points extracted from the news
4. A brief summary of implications
5. Any risk factors or caveats

Focus on how this news might affect the stock's price and investor sentiment.
"""

    return prompt


def _sanitize_text(text: Optional[str], max_length: int = 1000) -> str:
    """Sanitize and truncate text input."""
    if not text:
        return ""
    # Remove potential prompt injection attempts
    text = text.replace("```", "")
    text = text.replace("system:", "")
    text = text.replace("assistant:", "")
    text = text.replace("user:", "")
    # Truncate
    if len(text) > max_length:
        text = text[:max_length] + "..."
    return text


def _get_market_context(market: str) -> str:
    """Get market-specific context for the analysis."""
    contexts = {
        "US": """
- Consider US market dynamics, Fed policy impact
- Assess relevance to S&P 500 / NASDAQ trends
- Consider institutional investor perspective""",
        "HK": """
- Consider Hong Kong market dynamics
- Assess China policy and US-China relations impact
- Consider mainland investor flows (Southbound)""",
        "SH": """
- Consider A-share market dynamics
- Assess government policy and regulatory impact
- Consider retail investor sentiment (high retail participation)""",
        "SZ": """
- Consider Shenzhen market dynamics (tech/growth focus)
- Assess ChiNext/innovation sector trends
- Consider government policy support for tech sector""",
    }
    return contexts.get(market.upper(), contexts["US"])


def get_news_analysis_system_prompt() -> str:
    """Get the system prompt for news analysis."""
    return NEWS_ANALYSIS_SYSTEM_PROMPT
