"""Prompt templates for fundamental analysis agent."""

from typing import Any, Dict, Optional

from app.agents.prompts.sanitizer import (
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
- **资产负债率**: {_format_ratio(financials.get('debt_to_equity'))}
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
