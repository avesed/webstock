"""Prompt templates for fundamental analysis agent."""

from typing import Any, Dict, Optional

from app.prompts.analysis.sanitizer import (
    sanitize_dict_values,
    sanitize_input,
    sanitize_market,
    sanitize_symbol,
    MAX_DESCRIPTION_LENGTH,
)

# Market-specific context templates (English)
MARKET_CONTEXT_EN = {
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

# Market-specific context templates (Chinese)
MARKET_CONTEXT_ZH = {
    "us": """
你正在分析一只美股。请注意：
- SEC 披露文件和美国通用会计准则（GAAP）
- 美国市场交易时间（美东时间 9:30 AM - 4:00 PM）
- 美元计价的财务数据
- 美国估值基准（标普500市盈率平均约 20-25）
""",
    "hk": """
你正在分析一只港股。请注意：
- 港交所上市规则和香港会计准则
- 香港市场交易时间（9:30 AM - 4:00 PM HKT）
- 港币计价的财务数据（与美元挂钩）
- 与中国内地业务的关联
- 恒生指数作为基准
""",
    "sh": """
你正在分析一只上海A股。请注意：
- 中国证监会法规和中国会计准则
- 上海市场交易时间（9:30 AM - 3:00 PM 北京时间）
- 人民币计价的财务数据
- 国有持股和政策影响
- 上证综指作为基准
- 外资投资限制（QFII/沪港通）
""",
    "sz": """
你正在分析一只深圳A股。请注意：
- 中国证监会法规和中国会计准则
- 深圳市场交易时间（9:30 AM - 3:00 PM 北京时间）
- 人民币计价的财务数据
- 以科技和成长型公司为主
- 深证成指作为基准
- 外资投资限制（QFII/深港通）
""",
}

# Backward compatibility
MARKET_CONTEXT = MARKET_CONTEXT_EN

FUNDAMENTAL_SYSTEM_PROMPT_EN = """You are an expert fundamental analyst providing detailed stock analysis.

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

FUNDAMENTAL_SYSTEM_PROMPT_ZH = """你是一位专业的基本面分析师，提供详细的股票分析。

{market_context}

你的分析应当：
1. 以数据为驱动，客观公正
2. 清晰有条理
3. 聚焦投资含义
4. 关注风险与局限性

请使用格式良好的 Markdown 撰写分析报告，采用以下结构：

## 摘要
用2-3句话简要概述基本面评估结论。

## 估值评估
讨论该股票是被低估、估值合理还是被高估，并给出理由。

## 关键指标分析
分析：市盈率、营收/利润增长、盈利能力（利润率、ROE）和资产负债表健康度。使用要点列表以确保清晰。

## 优势
- 以要点形式列出基本面优势

## 劣势
- 以要点形式列出基本面劣势

## 风险因素
- 列出需要关注的主要风险

## 投资建议
给出你的操作建议（买入 / 持有 / 卖出 / 回避）并简述理由。

在 Markdown 分析之后，请附上一个用于机器解析的结构化数据块：

```json
{{
    "valuation_assessment": "undervalued|fairly_valued|overvalued",
    "valuation_confidence": "low|medium|high",
    "action": "buy|hold|sell|avoid",
    "strengths": ["优势1", "优势2"],
    "weaknesses": ["劣势1", "劣势2"],
    "risks": ["风险1", "风险2"]
}}
```
"""

# Backward compatibility
FUNDAMENTAL_SYSTEM_PROMPT = FUNDAMENTAL_SYSTEM_PROMPT_EN


def build_fundamental_prompt(
    symbol: str,
    market: str,
    info: Optional[Dict[str, Any]],
    financials: Optional[Dict[str, Any]],
    quote: Optional[Dict[str, Any]],
    language: str = "en",
    institutional_holders: Optional[Dict[str, Any]] = None,
    fund_holdings: Optional[Dict[str, Any]] = None,
    northbound_holding: Optional[Dict[str, Any]] = None,
    sector_industry: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Build the fundamental analysis prompt with stock data.

    Args:
        symbol: Stock symbol
        market: Market identifier (us, hk, sh, sz)
        info: Company information
        financials: Financial metrics
        quote: Current quote data
        language: Output language ('en' or 'zh')
        institutional_holders: US institutional holders (yfinance)
        fund_holdings: A-share fund holdings (AKShare)
        northbound_holding: A-share northbound holding (AKShare)
        sector_industry: Sector/industry info

    Returns:
        Formatted prompt string
    """
    # Sanitize inputs to prevent prompt injection
    symbol = sanitize_symbol(symbol)
    market = sanitize_market(market)

    # Select market context based on language
    market_contexts = MARKET_CONTEXT_ZH if language == "zh" else MARKET_CONTEXT_EN
    market_context = market_contexts.get(market.lower(), market_contexts["us"])

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
            info.get('description', 'No description available.' if language == "en" else '暂无描述。'),
            max_length=MAX_DESCRIPTION_LENGTH,
            field_name='description'
        )
        if language == "zh":
            info_text = f"""
## 公司信息
- **名称**: {info.get('name', '暂无')}
- **行业板块**: {info.get('sector', '暂无')}
- **细分行业**: {info.get('industry', '暂无')}
- **员工数**: {info.get('employees', '暂无')}
- **市值**: ${_format_number(info.get('market_cap'))}
- **货币**: {info.get('currency', '暂无')}
- **交易所**: {info.get('exchange', '暂无')}

### 业务描述
{description}
"""
        else:
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
        if language == "zh":
            financials_text = f"""
## 财务指标
### 估值
- **市盈率 (TTM)**: {_format_ratio(financials.get('pe_ratio'))}
- **前瞻市盈率**: {_format_ratio(financials.get('forward_pe'))}
- **市净率**: {_format_ratio(financials.get('price_to_book'))}
- **每股净资产**: ${_format_number(financials.get('book_value'))}

### 盈利能力
- **每股收益 (TTM)**: ${_format_number(financials.get('eps'))}
- **利润率**: {_format_percent(financials.get('profit_margin'))}
- **净资产收益率**: {_format_percent(financials.get('roe'))}

### 增长与分红
- **营收**: ${_format_number(financials.get('revenue'))}
- **股息收益率**: {_format_percent(financials.get('dividend_yield'))}
- **每股股息**: ${_format_number(financials.get('dividend_rate'))}

### 资产负债表
- **负债权益比 (D/E)**: {_format_ratio(financials.get('debt_to_equity'))}
"""
        else:
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
        if language == "zh":
            quote_text = f"""
## 当前市场数据
- **当前价格**: ${quote.get('price', '暂无')}
- **涨跌幅**: {quote.get('change', '暂无')} ({quote.get('change_percent', '暂无')}%)
- **日内区间**: ${quote.get('day_low', '暂无')} - ${quote.get('day_high', '暂无')}
- **成交量**: {_format_number(quote.get('volume'))}
- **昨收**: ${quote.get('previous_close', '暂无')}
"""
        else:
            quote_text = f"""
## Current Market Data
- **Current Price**: ${quote.get('price', 'N/A')}
- **Change**: {quote.get('change', 'N/A')} ({quote.get('change_percent', 'N/A')}%)
- **Day Range**: ${quote.get('day_low', 'N/A')} - ${quote.get('day_high', 'N/A')}
- **Volume**: {_format_number(quote.get('volume'))}
- **Previous Close**: ${quote.get('previous_close', 'N/A')}
"""
        data_sections.append(quote_text)

    # Institutional Holdings Section (US stocks)
    if institutional_holders and institutional_holders.get("holders"):
        holdings_section = _build_institutional_section(institutional_holders, language)
        if holdings_section:
            data_sections.append(holdings_section)

    # Fund Holdings Section (A-share stocks)
    if fund_holdings and fund_holdings.get("holdings"):
        fund_section = _build_fund_holdings_section(fund_holdings, language)
        if fund_section:
            data_sections.append(fund_section)

    # Northbound Holding Section (A-share stocks)
    if northbound_holding and northbound_holding.get("latest_holding"):
        nb_section = _build_northbound_section(northbound_holding, language)
        if nb_section:
            data_sections.append(nb_section)

    # Sector/Industry Section
    if sector_industry:
        sector_section = _build_sector_section(sector_industry, language)
        if sector_section:
            data_sections.append(sector_section)

    # Combine all sections
    data_content = "\n".join(data_sections) if data_sections else ("数据有限。" if language == "zh" else "Limited data available.")

    if language == "zh":
        user_prompt = f"""
# 基本面分析请求

**股票代码**: {symbol}
**市场**: {market.upper()}

{data_content}

---

请根据以上数据，对 {symbol} 进行全面的基本面分析。
重点关注估值、财务健康状况、盈利能力和投资意义。
如果数据缺失或不完整，请在分析中注明，并基于现有数据进行分析。
"""
    else:
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
        system_prompt = FUNDAMENTAL_SYSTEM_PROMPT_ZH
    else:
        market_contexts = MARKET_CONTEXT_EN
        system_prompt = FUNDAMENTAL_SYSTEM_PROMPT_EN

    market_context = market_contexts.get(market.lower(), market_contexts["us"])
    return system_prompt.format(market_context=market_context)


# Alias for backward compatibility
get_fundamental_system_prompt = get_system_prompt


def _build_institutional_section(
    data: Dict[str, Any],
    language: str
) -> str:
    """Build institutional holders section for US stocks."""
    holders = data.get("holders", [])
    if not holders:
        return ""

    # Show top 5 holders
    top_holders = holders[:5]

    if language == "zh":
        text = """
## 机构持仓
### 主要机构持有人
"""
        for i, h in enumerate(top_holders, 1):
            text += f"- **{i}. {h.get('holder', '未知')}**: "
            text += f"{_format_percent_value(h.get('pct_held'))} 持股比例, "
            text += f"{_format_number(h.get('shares'))} 股\n"

        total_pct = data.get("total_institutional_pct")
        if total_pct:
            text += f"\n**机构总持股比例**: {_format_percent_value(total_pct)}\n"
        if data.get("data_as_of"):
            text += f"**数据截止日期**: {data.get('data_as_of')}\n"
    else:
        text = """
## Institutional Holdings
### Top Institutional Holders
"""
        for i, h in enumerate(top_holders, 1):
            text += f"- **{i}. {h.get('holder', 'Unknown')}**: "
            text += f"{_format_percent_value(h.get('pct_held'))} ownership, "
            text += f"{_format_number(h.get('shares'))} shares\n"

        total_pct = data.get("total_institutional_pct")
        if total_pct:
            text += f"\n**Total Institutional Ownership**: {_format_percent_value(total_pct)}\n"
        if data.get("data_as_of"):
            text += f"**Data As Of**: {data.get('data_as_of')}\n"

    return text


def _build_fund_holdings_section(
    data: Dict[str, Any],
    language: str
) -> str:
    """Build fund holdings section for A-share stocks."""
    holdings = data.get("holdings")
    if not holdings:
        return ""

    if language == "zh":
        text = f"""
## 基金持仓（{data.get('quarter', '最新季度')}）
- **持仓机构数量**: {holdings.get('institution_count', '暂无')}
- **机构数变化**: {_format_change(holdings.get('institution_count_change'))}
- **持股比例**: {_format_percent_value(holdings.get('holding_pct'))}
- **持股比例变化**: {_format_change_pct(holdings.get('holding_pct_change'))}
- **占流通股比例**: {_format_percent_value(holdings.get('float_pct'))}
- **占流通股比例变化**: {_format_change_pct(holdings.get('float_pct_change'))}
"""
    else:
        text = f"""
## Fund Holdings ({data.get('quarter', 'Latest Quarter')})
- **Number of Institutions**: {holdings.get('institution_count', 'N/A')}
- **Institution Count Change**: {_format_change(holdings.get('institution_count_change'))}
- **Holding Percentage**: {_format_percent_value(holdings.get('holding_pct'))}
- **Holding % Change**: {_format_change_pct(holdings.get('holding_pct_change'))}
- **Float Percentage**: {_format_percent_value(holdings.get('float_pct'))}
- **Float % Change**: {_format_change_pct(holdings.get('float_pct_change'))}
"""
    return text


def _build_northbound_section(
    data: Dict[str, Any],
    language: str
) -> str:
    """Build northbound holding section for A-share stocks."""
    latest = data.get("latest_holding")
    if not latest:
        return ""

    if language == "zh":
        text = f"""
## 北向资金持仓
- **持股日期**: {latest.get('holding_date', '暂无')}
- **持股数量**: {_format_shares(latest.get('holding_shares'))}
- **持股市值**: {_format_cny(latest.get('holding_value'))}
- **占A股比例**: {_format_percent_value(latest.get('holding_pct'))}
"""
        if latest.get("change_shares") is not None:
            change = latest.get("change_shares")
            text += f"- **今日增持**: {'+' if change > 0 else ''}{_format_shares(change)}\n"
        if latest.get("change_value") is not None:
            change = latest.get("change_value")
            text += f"- **今日增持资金**: {'+' if change > 0 else ''}{_format_cny(change)}\n"
        if data.get("data_cutoff_notice"):
            text += f"\n**提示**: {data.get('data_cutoff_notice')}\n"
    else:
        text = f"""
## Northbound Holdings (Stock Connect)
- **Holding Date**: {latest.get('holding_date', 'N/A')}
- **Shares Held**: {_format_shares(latest.get('holding_shares'))}
- **Holding Value**: {_format_cny(latest.get('holding_value'))}
- **% of A-shares**: {_format_percent_value(latest.get('holding_pct'))}
"""
        if latest.get("change_shares") is not None:
            change = latest.get("change_shares")
            text += f"- **Today's Share Change**: {'+' if change > 0 else ''}{_format_shares(change)}\n"
        if latest.get("change_value") is not None:
            change = latest.get("change_value")
            text += f"- **Today's Value Change**: {'+' if change > 0 else ''}{_format_cny(change)}\n"
        if data.get("data_cutoff_notice"):
            text += f"\n**Notice**: {data.get('data_cutoff_notice')}\n"

    return text


def _build_sector_section(
    data: Dict[str, Any],
    language: str
) -> str:
    """Build sector/industry section."""
    # Handle both yfinance format and AKShare format
    sector = data.get("sector") or data.get("industry")
    industry = data.get("industry")

    if not sector and not industry:
        return ""

    if language == "zh":
        text = """
## 行业分类
"""
        if sector:
            text += f"- **行业大类**: {sector}\n"
        if industry and industry != sector:
            text += f"- **细分行业**: {industry}\n"
    else:
        text = """
## Sector & Industry
"""
        if sector:
            text += f"- **Sector**: {sector}\n"
        if industry and industry != sector:
            text += f"- **Industry**: {industry}\n"

    return text


def _format_percent_value(value: Optional[float]) -> str:
    """Format a percentage value (handles both 0.15 and 15.0 formats)."""
    if value is None:
        return "N/A"
    # If value is less than 1, assume it's a decimal (0.15 = 15%)
    if abs(value) < 1:
        return f"{value * 100:.2f}%"
    return f"{value:.2f}%"


def _format_change(value: Optional[int]) -> str:
    """Format a change value with sign."""
    if value is None:
        return "N/A"
    return f"{'+' if value > 0 else ''}{value}"


def _format_change_pct(value: Optional[float]) -> str:
    """Format a percentage change with sign."""
    if value is None:
        return "N/A"
    return f"{'+' if value > 0 else ''}{value:.2f}%"


def _format_shares(value: Optional[float]) -> str:
    """Format share count for display."""
    if value is None:
        return "N/A"
    if abs(value) >= 1e8:
        return f"{value / 1e8:.2f}亿股"
    if abs(value) >= 1e4:
        return f"{value / 1e4:.2f}万股"
    return f"{value:.0f}股"


def _format_cny(value: Optional[float]) -> str:
    """Format CNY value for display."""
    if value is None:
        return "N/A"
    if abs(value) >= 1e8:
        return f"¥{value / 1e8:.2f}亿"
    if abs(value) >= 1e4:
        return f"¥{value / 1e4:.2f}万"
    return f"¥{value:.2f}"


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
    """
    Format a percentage for display.

    Assumes input is in decimal format (0.15 = 15%, 1.52 = 152%).
    This is consistent with yfinance's profitMargins, returnOnEquity, etc.
    Note: dividendYield is normalized to decimal in stock_service.py.
    """
    if value is None:
        return "N/A"
    # Always multiply by 100 since input is decimal format
    return f"{value * 100:.2f}%"
