"""Prompt templates for sentiment analysis agent."""

from typing import Any, Dict, List, Optional

from app.agents.prompts.sanitizer import (
    sanitize_dict_values,
    sanitize_market,
    sanitize_news_article,
    sanitize_symbol,
)

# Market-specific context templates (English)
MARKET_CONTEXT_EN = {
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

# Market-specific context templates (Chinese)
MARKET_CONTEXT_ZH = {
    "us": """
你正在分析一只美股的市场情绪。请注意：
- 美国经济数据的影响（美联储决策、就业、通胀）
- 财报季动态
- 分析师评级和目标价
- 社交媒体和散户情绪
- 机构持仓变化（13F文件）
- 板块轮动模式
""",
    "hk": """
你正在分析一只港股的市场情绪。请注意：
- 中国政策公告的影响
- 中美关系
- 内地投资者情绪（南向资金）
- 香港政治和经济动态
- 双重上市股票动态（ADR/H股）
- 区域资金流向
""",
    "sh": """
你正在分析一只上海A股的市场情绪。请注意：
- 政府政策和监管变化
- 官方媒体评论
- 北向（外资）资金流向
- 散户行为（散户参与度高）
- 融资融券数据
- 行业政策支持或限制
""",
    "sz": """
你正在分析一只深圳A股的市场情绪。请注意：
- 科技创新政策支持
- 创业板/科创板特有情绪
- 散户投机模式
- 北向资金流向
- IPO和增发活动
- 交叉持股和关联方动态
""",
}

# Backward compatibility
MARKET_CONTEXT = MARKET_CONTEXT_EN

SENTIMENT_SYSTEM_PROMPT_EN = """You are an expert market sentiment analyst providing comprehensive sentiment assessment.

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

SENTIMENT_SYSTEM_PROMPT_ZH = """你是一位专业的市场情绪分析师，提供全面的情绪评估。

{market_context}

你的分析应当考虑：
1. 价格动量作为情绪指标
2. 成交量模式反映的市场信心
3. 市场背景和板块趋势
4. 新闻影响（如有）
5. 技术面情绪指标

请使用格式良好的 Markdown 撰写分析报告，采用以下结构：

## 摘要
用2-3句话简要概述整体情绪。

## 整体情绪
给出情绪判断（极度看空 / 看空 / 中性 / 看多 / 极度看多），打分（-100到100），以及置信度。

## 价格动量
评估近期价格走势：强势下跌、下跌、震荡、上涨或强势上涨。解释你的理由。

## 成交量情绪
评估成交量模式：派发、中性或吸筹。解释你的理由。

## 市场背景
讨论板块趋势、大盘情绪，以及该股票相对于同业/大盘的表现。

## 新闻情绪
如有新闻，分析关键主题及其影响。如无新闻，请注明。

## 风险因素
- 列出需要关注的情绪相关风险

## 催化剂
### 多头催化剂
- 列出潜在的利好因素

### 空头催化剂
- 列出潜在的利空因素

## 建议
给出你的情绪偏向（看多 / 中性 / 看空），时机考量及理由。

在 Markdown 分析之后，请附上一个用于机器解析的结构化数据块：

```json
{{
    "sentiment_score": <-100到100的数字>,
    "sentiment_label": "very_bearish|bearish|neutral|bullish|very_bullish",
    "confidence": "low|medium|high",
    "momentum": "strong_downtrend|downtrend|neutral|uptrend|strong_uptrend",
    "volume": "distribution|neutral|accumulation",
    "sentiment_bias": "bullish|neutral|bearish"
}}
```
"""

# Backward compatibility
SENTIMENT_SYSTEM_PROMPT = SENTIMENT_SYSTEM_PROMPT_EN


def build_sentiment_prompt(
    symbol: str,
    market: str,
    quote: Optional[Dict[str, Any]],
    history_summary: Optional[Dict[str, Any]],
    news: Optional[List[Dict[str, Any]]],
    market_context: Optional[Dict[str, Any]],
    language: str = "en",
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
        language: Output language ('en' or 'zh')

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
        if language == "zh":
            quote_text = f"""
## 当前市场数据
- **当前价格**: ${quote.get('price', '暂无')}
- **日涨跌**: {quote.get('change', '暂无')} ({quote.get('change_percent', '暂无')}%)
- **日内区间**: ${quote.get('day_low', '暂无')} - ${quote.get('day_high', '暂无')}
- **成交量**: {_format_volume(quote.get('volume'))}
- **昨收价**: ${quote.get('previous_close', '暂无')}
"""
        else:
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
        if language == "zh":
            momentum_text = f"""
## 价格动量分析
### 表现
- **1日涨跌**: {_format_percent(history_summary.get('change_1d'))}
- **1周涨跌**: {_format_percent(history_summary.get('change_1w'))}
- **1月涨跌**: {_format_percent(history_summary.get('change_1m'))}
- **3月涨跌**: {_format_percent(history_summary.get('change_3m'))}
- **年初至今**: {_format_percent(history_summary.get('change_ytd'))}
- **1年涨跌**: {_format_percent(history_summary.get('change_1y'))}

### 趋势指标
- **52周最高**: ${history_summary.get('high_52w', '暂无')}
- **52周最低**: ${history_summary.get('low_52w', '暂无')}
- **距52周高点**: {_format_percent(history_summary.get('pct_from_high'))}
- **距52周低点**: {_format_percent(history_summary.get('pct_from_low'))}

### 成交量分析
- **20日平均成交量**: {_format_volume(history_summary.get('avg_volume_20d'))}
- **成交量 vs 均量**: {_format_volume_ratio(history_summary.get('volume_ratio'), language)}
- **成交量趋势**: {history_summary.get('volume_trend', '暂无')}

### 波动率
- **20日波动率**: {_format_percent(history_summary.get('volatility_20d'))}
- **波动率排名**: {history_summary.get('volatility_rank', '暂无')}
"""
        else:
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
- **Volume vs Average**: {_format_volume_ratio(history_summary.get('volume_ratio'), language)}
- **Volume Trend**: {history_summary.get('volume_trend', 'N/A')}

### Volatility
- **20-Day Volatility**: {_format_percent(history_summary.get('volatility_20d'))}
- **Volatility Rank**: {history_summary.get('volatility_rank', 'N/A')}
"""
        data_sections.append(momentum_text)

    # News Section
    if news and len(news) > 0:
        if language == "zh":
            news_text = """
## 近期新闻
"""
            for i, article in enumerate(news[:5], 1):
                news_text += f"""
### {i}. {article.get('title', '无标题')}
- **来源**: {article.get('source', '未知')}
- **发布时间**: {article.get('published_at', '暂无')}
- **摘要**: {article.get('summary', '暂无摘要')[:200]}
"""
        else:
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
        if language == "zh":
            data_sections.append("""
## 新闻
暂无近期新闻可供分析。情绪评估将主要基于价格走势和市场背景。
""")
        else:
            data_sections.append("""
## News
No recent news available for analysis. Sentiment will be assessed primarily based on price action and market context.
""")

    # Market Context Section
    if market_context:
        if language == "zh":
            context_text = f"""
## 市场背景
### 大盘情况
- **指数涨跌**: {_format_percent(market_context.get('index_change'))}
- **市场趋势**: {market_context.get('market_trend', '暂无')}
- **板块表现**: {_format_percent(market_context.get('sector_change'))}
- **板块趋势**: {market_context.get('sector_trend', '暂无')}

### 相对表现
- **相对大盘**: {_format_percent(market_context.get('vs_market'))}
- **相对板块**: {_format_percent(market_context.get('vs_sector'))}
- **相对强度**: {market_context.get('relative_strength', '暂无')}
"""
        else:
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
    data_content = "\n".join(data_sections) if data_sections else ("数据有限。" if language == "zh" else "Limited data available.")

    if language == "zh":
        user_prompt = f"""
# 情绪分析请求

**股票代码**: {symbol}
**市场**: {market.upper()}

{data_content}

---

请根据以上数据，对 {symbol} 进行全面的情绪分析。
重点关注价格动量、成交量模式、新闻影响（如有）和整体市场情绪。
评估近期价格走势背后的市场信心，并识别潜在的情绪变化。
如果数据缺失或不完整，请在分析中注明，并基于现有数据进行分析。
"""
    else:
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
        system_prompt = SENTIMENT_SYSTEM_PROMPT_ZH
    else:
        market_contexts = MARKET_CONTEXT_EN
        system_prompt = SENTIMENT_SYSTEM_PROMPT_EN

    market_context = market_contexts.get(market.lower(), market_contexts["us"])
    return system_prompt.format(market_context=market_context)


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


def _format_volume_ratio(ratio: Optional[float], language: str = "en") -> str:
    """Format volume ratio for display."""
    if ratio is None:
        return "N/A"
    if language == "zh":
        if ratio > 2.0:
            return f"{ratio:.2f}x (非常放量)"
        if ratio > 1.5:
            return f"{ratio:.2f}x (放量)"
        if ratio < 0.5:
            return f"{ratio:.2f}x (缩量)"
        return f"{ratio:.2f}x (正常)"
    else:
        if ratio > 2.0:
            return f"{ratio:.2f}x (Very High)"
        if ratio > 1.5:
            return f"{ratio:.2f}x (High)"
        if ratio < 0.5:
            return f"{ratio:.2f}x (Low)"
        return f"{ratio:.2f}x (Normal)"
