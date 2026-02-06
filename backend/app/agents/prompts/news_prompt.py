"""Prompt templates for news analysis."""

from typing import Any, Dict, Optional

from app.agents.prompts.sanitizer import sanitize_dict_values, sanitize_symbol


# English system prompt
NEWS_ANALYSIS_SYSTEM_PROMPT_EN = """You are an expert financial news analyst specializing in assessing news impact on stock prices.

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

# Chinese system prompt
NEWS_ANALYSIS_SYSTEM_PROMPT_ZH = """你是一位专业的财经新闻分析师，专注于评估新闻对股价的影响。

你的分析应该：
1. 客观公正
2. 聚焦市场相关信息
3. 信息有限时明确说明不确定性
4. 兼顾短期和长期影响

请始终使用以下JSON格式返回分析结果：
{
    "sentiment_score": <-1.0到1.0之间的数字>,
    "sentiment_label": "positive" | "negative" | "neutral",
    "impact_prediction": {
        "direction": "bullish" | "bearish" | "neutral",
        "magnitude": "high" | "medium" | "low",
        "timeframe": "immediate" | "short_term" | "long_term",
        "confidence": "high" | "medium" | "low"
    },
    "key_points": [
        "关键要点1",
        "关键要点2",
        "关键要点3"
    ],
    "summary": "2-3句话简述新闻内容及其对股价的影响",
    "risk_factors": [
        "风险或注意事项1",
        "风险或注意事项2"
    ],
    "related_themes": [
        "主题1（如：财报、监管、竞争）",
        "主题2"
    ]
}

情绪评分指南：
- 1.0: 极度正面（重大利好催化剂、溢价收购、突破性进展）
- 0.5 到 0.9: 正面（业绩超预期、新产品发布、业务扩张）
- 0.1 到 0.4: 轻微正面（小利好、重申业绩指引）
- -0.1 到 0.1: 中性（常规新闻、信号混杂）
- -0.4 到 -0.1: 轻微负面（小问题、业绩符合预期）
- -0.9 到 -0.5: 负面（业绩不及预期、监管问题）
- -1.0: 极度负面（重大丑闻、破产风险、欺诈）
"""

# Backward compatibility alias
NEWS_ANALYSIS_SYSTEM_PROMPT = NEWS_ANALYSIS_SYSTEM_PROMPT_EN


def build_news_analysis_prompt(
    symbol: str,
    title: str,
    summary: Optional[str],
    source: str,
    published_at: str,
    market: str,
    additional_context: Optional[Dict[str, Any]] = None,
    language: str = "en",
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
        language: Language code ("en" or "zh")

    Returns:
        Formatted prompt string
    """
    # Sanitize inputs
    symbol = sanitize_symbol(symbol)
    title = _sanitize_text(title, max_length=500)
    no_summary_text = "暂无摘要。" if language == "zh" else "No summary available."
    summary = _sanitize_text(summary, max_length=2000) if summary else no_summary_text
    source = _sanitize_text(source, max_length=100)

    if additional_context:
        additional_context = sanitize_dict_values(additional_context)

    # Build market context
    market_context = _get_market_context(market, language)

    # Build additional context section
    context_section = ""
    if additional_context:
        if language == "zh":
            context_section = f"""
## 当前股票信息
- **当前价格**: ${additional_context.get('price', 'N/A')}
- **日涨跌幅**: {additional_context.get('change_percent', 'N/A')}%
- **市值**: {additional_context.get('market_cap', 'N/A')}
- **行业**: {additional_context.get('sector', 'N/A')}
"""
        else:
            context_section = f"""
## Current Stock Context
- **Current Price**: ${additional_context.get('price', 'N/A')}
- **Daily Change**: {additional_context.get('change_percent', 'N/A')}%
- **Market Cap**: {additional_context.get('market_cap', 'N/A')}
- **Sector**: {additional_context.get('sector', 'N/A')}
"""

    if language == "zh":
        prompt = f"""
# 新闻分析请求

## 股票信息
- **代码**: {symbol}
- **市场**: {market}
{market_context}

## 新闻文章
- **来源**: {source}
- **发布时间**: {published_at}

### 标题
{title}

### 内容/摘要
{summary}
{context_section}

---

请分析这篇新闻并提供：
1. 情绪评分（-1.0 到 1.0）
2. 对股价的影响预测
3. 从新闻中提取的关键要点
4. 简要总结新闻的影响
5. 任何风险因素或注意事项

重点分析这则新闻可能如何影响股票价格和投资者情绪。
"""
    else:
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


def _get_market_context(market: str, language: str = "en") -> str:
    """Get market-specific context for the analysis."""
    if language == "zh":
        contexts = {
            "US": """
- 考虑美国市场动态和美联储政策影响
- 评估与标普500/纳斯达克指数的相关性
- 从机构投资者角度分析""",
            "HK": """
- 考虑香港市场动态
- 评估中国政策和中美关系的影响
- 考虑南向资金流动情况""",
            "SH": """
- 考虑A股市场动态
- 评估政府政策和监管影响
- 考虑散户投资者情绪（散户参与度高）""",
            "SZ": """
- 考虑深圳市场动态（科技/成长股为主）
- 评估创业板/创新板块趋势
- 考虑政府对科技行业的政策支持""",
        }
    else:
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


def get_news_analysis_system_prompt(language: str = "en") -> str:
    """Get the system prompt for news analysis based on language."""
    if language == "zh":
        return NEWS_ANALYSIS_SYSTEM_PROMPT_ZH
    return NEWS_ANALYSIS_SYSTEM_PROMPT_EN
