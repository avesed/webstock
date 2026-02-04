"""Prompt templates for fundamental analysis agent."""

from typing import Any, Dict, Optional

from app.agents.prompts.sanitizer import (
    sanitize_dict_values,
    sanitize_input,
    sanitize_market,
    sanitize_symbol,
    MAX_DESCRIPTION_LENGTH,
)

# Market-specific context templates
MARKET_CONTEXT = {
    "us": """
You are analyzing a US-listed stock. Consider:
- SEC filings and GAAP accounting standards
- US market trading hours (9:30 AM - 4:00 PM ET)
- Dollar-denominated financials
- Common US valuation benchmarks (S&P 500 P/E average ~20-25)
""",
    "hk": """
You are analyzing a Hong Kong-listed stock. Consider:
- HKEX listing rules and Hong Kong accounting standards
- HK market trading hours (9:30 AM - 4:00 PM HKT)
- HKD-denominated financials (pegged to USD)
- Potential mainland China business exposure
- Hang Seng Index as benchmark
""",
    "sh": """
You are analyzing a Shanghai A-share stock. Consider:
- CSRC regulations and Chinese accounting standards
- Shanghai market trading hours (9:30 AM - 3:00 PM CST)
- CNY-denominated financials
- State ownership and policy influence
- SSE Composite Index as benchmark
- Foreign investment restrictions (QFII/Stock Connect)
""",
    "sz": """
You are analyzing a Shenzhen A-share stock. Consider:
- CSRC regulations and Chinese accounting standards
- Shenzhen market trading hours (9:30 AM - 3:00 PM CST)
- CNY-denominated financials
- Tech and growth company focus
- SZSE Component Index as benchmark
- Foreign investment restrictions (QFII/Stock Connect)
""",
}

FUNDAMENTAL_SYSTEM_PROMPT = """You are an expert fundamental analyst providing detailed stock analysis.

{market_context}

Your analysis should be:
1. Data-driven and objective
2. Clear and structured
3. Focused on investment implications
4. Mindful of risks and limitations

Write your analysis in well-formatted Markdown. Use the following structure:

## Summary
A concise 2-3 sentence overview of the fundamental assessment.

## Valuation Assessment
Discuss whether the stock appears undervalued, fairly valued, or overvalued, with reasoning.

## Key Metrics Analysis
Analyze: P/E ratio, revenue/earnings growth, profitability (margins, ROE), and balance sheet health. Use bullet points for clarity.

## Strengths
- List fundamental strengths as bullet points

## Weaknesses
- List fundamental weaknesses as bullet points

## Risk Factors
- List key risks to consider

## Recommendation
State your action (Buy / Hold / Sell / Avoid) with a brief rationale.

After your Markdown analysis, include a structured data block for machine parsing:

```json
{{
    "valuation_assessment": "undervalued|fairly_valued|overvalued",
    "valuation_confidence": "low|medium|high",
    "action": "buy|hold|sell|avoid",
    "strengths": ["strength1", "strength2"],
    "weaknesses": ["weakness1", "weakness2"],
    "risks": ["risk1", "risk2"]
}}
```
"""


def build_fundamental_prompt(
    symbol: str,
    market: str,
    info: Optional[Dict[str, Any]],
    financials: Optional[Dict[str, Any]],
    quote: Optional[Dict[str, Any]],
) -> str:
    """
    Build the fundamental analysis prompt with stock data.

    Args:
        symbol: Stock symbol
        market: Market identifier (us, hk, sh, sz)
        info: Company information
        financials: Financial metrics
        quote: Current quote data

    Returns:
        Formatted prompt string
    """
    # Sanitize inputs to prevent prompt injection
    symbol = sanitize_symbol(symbol)
    market = sanitize_market(market)

    market_context = MARKET_CONTEXT.get(market.lower(), MARKET_CONTEXT["us"])

    # Sanitize data dictionaries
    info = sanitize_dict_values(info) if info else None
    financials = sanitize_dict_values(financials) if financials else None
    quote = sanitize_dict_values(quote) if quote else None

    # Build data sections
    data_sections = []

    # Company Info Section
    if info:
        # Sanitize description with specific length limit
        description = sanitize_input(
            info.get('description', 'No description available.'),
            max_length=MAX_DESCRIPTION_LENGTH,
            field_name='description'
        )
        info_text = f"""
## Company Information
- **Name**: {info.get('name', 'N/A')}
- **Sector**: {info.get('sector', 'N/A')}
- **Industry**: {info.get('industry', 'N/A')}
- **Employees**: {info.get('employees', 'N/A')}
- **Market Cap**: ${_format_number(info.get('market_cap'))}
- **Currency**: {info.get('currency', 'N/A')}
- **Exchange**: {info.get('exchange', 'N/A')}

### Business Description
{description}
"""
        data_sections.append(info_text)

    # Financial Metrics Section
    if financials:
        financials_text = f"""
## Financial Metrics
### Valuation
- **P/E Ratio (TTM)**: {_format_ratio(financials.get('pe_ratio'))}
- **Forward P/E**: {_format_ratio(financials.get('forward_pe'))}
- **Price to Book**: {_format_ratio(financials.get('price_to_book'))}
- **Book Value**: ${_format_number(financials.get('book_value'))}

### Profitability
- **EPS (TTM)**: ${_format_number(financials.get('eps'))}
- **Profit Margin**: {_format_percent(financials.get('profit_margin'))}
- **ROE**: {_format_percent(financials.get('roe'))}

### Growth & Income
- **Revenue**: ${_format_number(financials.get('revenue'))}
- **Dividend Yield**: {_format_percent(financials.get('dividend_yield'))}
- **Dividend Rate**: ${_format_number(financials.get('dividend_rate'))}

### Balance Sheet
- **Debt to Equity**: {_format_ratio(financials.get('debt_to_equity'))}
"""
        data_sections.append(financials_text)

    # Current Quote Section
    if quote:
        quote_text = f"""
## Current Market Data
- **Current Price**: ${quote.get('price', 'N/A')}
- **Change**: {quote.get('change', 'N/A')} ({quote.get('change_percent', 'N/A')}%)
- **Day Range**: ${quote.get('day_low', 'N/A')} - ${quote.get('day_high', 'N/A')}
- **Volume**: {_format_number(quote.get('volume'))}
- **Previous Close**: ${quote.get('previous_close', 'N/A')}
"""
        data_sections.append(quote_text)

    # Combine all sections
    data_content = "\n".join(data_sections) if data_sections else "Limited data available."

    user_prompt = f"""
# Fundamental Analysis Request

**Stock Symbol**: {symbol}
**Market**: {market.upper()}

{data_content}

---

Please provide a comprehensive fundamental analysis of {symbol} based on the above data.
Focus on valuation, financial health, profitability, and investment implications.
If data is missing or incomplete, note this in your analysis and work with what's available.
"""

    return user_prompt


def get_system_prompt(market: str) -> str:
    """Get the system prompt with market-specific context."""
    market_context = MARKET_CONTEXT.get(market.lower(), MARKET_CONTEXT["us"])
    return FUNDAMENTAL_SYSTEM_PROMPT.format(market_context=market_context)


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


def _format_ratio(value: Optional[float]) -> str:
    """Format a ratio for display."""
    if value is None:
        return "N/A"
    return f"{value:.2f}"


def _format_percent(value: Optional[float]) -> str:
    """Format a percentage for display."""
    if value is None:
        return "N/A"
    # Handle both decimal (0.15) and percentage (15.0) formats
    if abs(value) < 1:
        return f"{value * 100:.2f}%"
    return f"{value:.2f}%"
