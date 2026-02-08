"""Structured output schemas for LangGraph-based AI analysis agents.

This module defines Pydantic schemas for standardized analysis results
from the layered LLM architecture. All schemas inherit from CamelModel
for consistent API serialization.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import Field

from app.schemas.base import CamelModel


class AnalysisConfidence(str, Enum):
    """Confidence level for analysis results."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ActionRecommendation(str, Enum):
    """Investment action recommendation."""

    STRONG_BUY = "strong_buy"
    BUY = "buy"
    HOLD = "hold"
    SELL = "sell"
    STRONG_SELL = "strong_sell"
    AVOID = "avoid"


class ValuationAssessment(str, Enum):
    """Valuation assessment result."""

    UNDERVALUED = "undervalued"
    FAIRLY_VALUED = "fairly_valued"
    OVERVALUED = "overvalued"


class TrendDirection(str, Enum):
    """Price trend direction."""

    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class SentimentLevel(str, Enum):
    """Market sentiment level."""

    VERY_POSITIVE = "very_positive"
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    VERY_NEGATIVE = "very_negative"


class ClarificationType(str, Enum):
    """Type of clarification needed from user."""

    MISSING_DATA = "missing_data"
    AMBIGUOUS_QUERY = "ambiguous_query"
    TIME_PERIOD = "time_period"
    MARKET_SELECTION = "market_selection"
    ANALYSIS_SCOPE = "analysis_scope"


# ============================================================================
# Clarification Request Schema
# ============================================================================


class ClarificationOption(CamelModel):
    """An option for clarifying user intent."""

    value: str = Field(..., description="The option value")
    label: str = Field(..., description="Display label for the option")
    label_zh: Optional[str] = Field(None, description="Chinese display label")


class ClarificationRequest(CamelModel):
    """Request for user clarification when analysis cannot proceed."""

    clarification_type: ClarificationType = Field(
        ...,
        description="Type of clarification needed"
    )
    question: str = Field(..., description="Question to ask the user (English)")
    question_zh: Optional[str] = Field(None, description="Question in Chinese")
    options: Optional[List[ClarificationOption]] = Field(
        None,
        description="Available options for user selection"
    )
    default_value: Optional[str] = Field(
        None,
        description="Default value if user doesn't specify"
    )
    context: Optional[Dict[str, Any]] = Field(
        None,
        description="Additional context for the clarification"
    )


# ============================================================================
# Key Insight Schema (used across multiple agents)
# ============================================================================


class KeyInsight(CamelModel):
    """A key insight or finding from analysis."""

    title: str = Field(..., description="Short title for the insight")
    description: str = Field(..., description="Detailed description")
    importance: AnalysisConfidence = Field(
        AnalysisConfidence.MEDIUM,
        description="Importance level of this insight"
    )
    category: Optional[str] = Field(None, description="Category of insight")


# ============================================================================
# Fundamental Analysis Schema
# ============================================================================


class FundamentalMetrics(CamelModel):
    """Key fundamental metrics extracted from analysis."""

    pe_ratio: Optional[float] = Field(None, description="P/E ratio (TTM)")
    forward_pe: Optional[float] = Field(None, description="Forward P/E ratio")
    price_to_book: Optional[float] = Field(None, description="Price to book ratio")
    price_to_sales: Optional[float] = Field(None, description="Price to sales ratio")
    roe: Optional[float] = Field(None, description="Return on equity (%)")
    roa: Optional[float] = Field(None, description="Return on assets (%)")
    profit_margin: Optional[float] = Field(None, description="Profit margin (%)")
    debt_to_equity: Optional[float] = Field(None, description="Debt to equity ratio")
    current_ratio: Optional[float] = Field(None, description="Current ratio")
    revenue_growth: Optional[float] = Field(None, description="Revenue growth (%)")
    earnings_growth: Optional[float] = Field(None, description="Earnings growth (%)")


class FundamentalAnalysisResult(CamelModel):
    """Structured result from fundamental analysis agent."""

    valuation: ValuationAssessment = Field(
        ...,
        description="Overall valuation assessment"
    )
    valuation_confidence: AnalysisConfidence = Field(
        AnalysisConfidence.MEDIUM,
        description="Confidence in valuation assessment"
    )
    action: ActionRecommendation = Field(
        ...,
        description="Recommended investment action"
    )
    target_price: Optional[float] = Field(
        None,
        description="Target price if applicable"
    )
    metrics: Optional[FundamentalMetrics] = Field(
        None,
        description="Key fundamental metrics"
    )
    strengths: List[str] = Field(
        default_factory=list,
        description="List of fundamental strengths"
    )
    weaknesses: List[str] = Field(
        default_factory=list,
        description="List of fundamental weaknesses"
    )
    risks: List[str] = Field(
        default_factory=list,
        description="Key risk factors"
    )
    key_insights: List[KeyInsight] = Field(
        default_factory=list,
        description="Key insights from the analysis"
    )
    summary: str = Field(..., description="Brief summary of the analysis")


# ============================================================================
# Technical Analysis Schema
# ============================================================================


class TechnicalIndicators(CamelModel):
    """Technical analysis indicators."""

    rsi: Optional[float] = Field(None, description="Relative Strength Index (0-100)")
    macd: Optional[float] = Field(None, description="MACD line value")
    macd_signal: Optional[float] = Field(None, description="MACD signal line value")
    macd_histogram: Optional[float] = Field(None, description="MACD histogram value")
    sma_20: Optional[float] = Field(None, description="20-day SMA")
    sma_50: Optional[float] = Field(None, description="50-day SMA")
    sma_200: Optional[float] = Field(None, description="200-day SMA")
    ema_12: Optional[float] = Field(None, description="12-day EMA")
    ema_26: Optional[float] = Field(None, description="26-day EMA")
    bollinger_upper: Optional[float] = Field(None, description="Bollinger upper band")
    bollinger_lower: Optional[float] = Field(None, description="Bollinger lower band")
    atr: Optional[float] = Field(None, description="Average True Range")
    volume_sma: Optional[float] = Field(None, description="Volume SMA")


class SupportResistanceLevel(CamelModel):
    """Support or resistance price level."""

    price: float = Field(..., description="Price level")
    strength: AnalysisConfidence = Field(
        AnalysisConfidence.MEDIUM,
        description="Strength of the level"
    )
    level_type: str = Field(..., description="'support' or 'resistance'")


class TechnicalAnalysisResult(CamelModel):
    """Structured result from technical analysis agent."""

    trend: TrendDirection = Field(..., description="Overall trend direction")
    trend_strength: AnalysisConfidence = Field(
        AnalysisConfidence.MEDIUM,
        description="Strength of the trend"
    )
    action: ActionRecommendation = Field(
        ...,
        description="Recommended action based on technicals"
    )
    indicators: Optional[TechnicalIndicators] = Field(
        None,
        description="Technical indicator values"
    )
    support_levels: List[SupportResistanceLevel] = Field(
        default_factory=list,
        description="Key support levels"
    )
    resistance_levels: List[SupportResistanceLevel] = Field(
        default_factory=list,
        description="Key resistance levels"
    )
    signals: List[str] = Field(
        default_factory=list,
        description="Active technical signals"
    )
    pattern_detected: Optional[str] = Field(
        None,
        description="Chart pattern detected, if any"
    )
    key_insights: List[KeyInsight] = Field(
        default_factory=list,
        description="Key insights from the analysis"
    )
    summary: str = Field(..., description="Brief summary of the analysis")


# ============================================================================
# Sentiment Analysis Schema
# ============================================================================


class SentimentSource(CamelModel):
    """Sentiment data from a specific source."""

    source: str = Field(..., description="Source name (e.g., 'social_media', 'news')")
    sentiment: SentimentLevel = Field(..., description="Sentiment level")
    score: Optional[float] = Field(
        None,
        description="Numeric sentiment score (-1 to 1)"
    )
    sample_size: Optional[int] = Field(
        None,
        description="Number of data points analyzed"
    )


class SentimentAnalysisResult(CamelModel):
    """Structured result from sentiment analysis agent."""

    overall_sentiment: SentimentLevel = Field(
        ...,
        description="Overall market sentiment"
    )
    sentiment_score: Optional[float] = Field(
        None,
        description="Numeric sentiment score (-1 to 1)"
    )
    sentiment_trend: TrendDirection = Field(
        TrendDirection.NEUTRAL,
        description="Direction of sentiment change"
    )
    confidence: AnalysisConfidence = Field(
        AnalysisConfidence.MEDIUM,
        description="Confidence in sentiment assessment"
    )
    sources: List[SentimentSource] = Field(
        default_factory=list,
        description="Sentiment breakdown by source"
    )
    key_themes: List[str] = Field(
        default_factory=list,
        description="Key themes in market discussion"
    )
    bullish_factors: List[str] = Field(
        default_factory=list,
        description="Factors driving positive sentiment"
    )
    bearish_factors: List[str] = Field(
        default_factory=list,
        description="Factors driving negative sentiment"
    )
    key_insights: List[KeyInsight] = Field(
        default_factory=list,
        description="Key insights from the analysis"
    )
    summary: str = Field(..., description="Brief summary of the analysis")


# ============================================================================
# News Analysis Schema
# ============================================================================


class NewsItem(CamelModel):
    """A news item included in analysis."""

    title: str = Field(..., description="News headline")
    source: Optional[str] = Field(None, description="News source")
    published_at: Optional[datetime] = Field(None, description="Publication time")
    sentiment: Optional[SentimentLevel] = Field(None, description="Article sentiment")
    relevance_score: Optional[float] = Field(
        None,
        description="Relevance score (0-1)"
    )
    summary: Optional[str] = Field(None, description="Brief article summary")


class NewsAnalysisResult(CamelModel):
    """Structured result from news analysis agent."""

    overall_sentiment: SentimentLevel = Field(
        ...,
        description="Overall news sentiment"
    )
    news_volume: str = Field(
        "normal",
        description="News volume level: 'low', 'normal', 'high', 'very_high'"
    )
    news_trend: TrendDirection = Field(
        TrendDirection.NEUTRAL,
        description="Direction of news sentiment"
    )
    confidence: AnalysisConfidence = Field(
        AnalysisConfidence.MEDIUM,
        description="Confidence in news assessment"
    )
    top_news: List[NewsItem] = Field(
        default_factory=list,
        description="Most relevant news items"
    )
    key_events: List[str] = Field(
        default_factory=list,
        description="Key events mentioned in news"
    )
    positive_themes: List[str] = Field(
        default_factory=list,
        description="Positive themes in news coverage"
    )
    negative_themes: List[str] = Field(
        default_factory=list,
        description="Negative themes in news coverage"
    )
    upcoming_events: List[str] = Field(
        default_factory=list,
        description="Upcoming events that may impact stock"
    )
    key_insights: List[KeyInsight] = Field(
        default_factory=list,
        description="Key insights from the analysis"
    )
    summary: str = Field(..., description="Brief summary of the analysis")


# ============================================================================
# Unified Agent Analysis Result
# ============================================================================


class AgentAnalysisResult(CamelModel):
    """
    Standardized analysis result wrapper for all agent types.

    This provides a consistent interface for the orchestrator and synthesis layer.
    """

    agent_type: str = Field(..., description="Type of agent that produced this result")
    symbol: str = Field(..., description="Stock symbol analyzed")
    market: str = Field(..., description="Market identifier")
    success: bool = Field(..., description="Whether analysis succeeded")
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="Analysis timestamp"
    )
    latency_ms: int = Field(0, description="Analysis latency in milliseconds")
    tokens_used: int = Field(0, description="Tokens consumed for this analysis")

    # Result content (one of these will be populated based on agent type)
    fundamental: Optional[FundamentalAnalysisResult] = Field(
        None,
        description="Fundamental analysis result"
    )
    technical: Optional[TechnicalAnalysisResult] = Field(
        None,
        description="Technical analysis result"
    )
    sentiment: Optional[SentimentAnalysisResult] = Field(
        None,
        description="Sentiment analysis result"
    )
    news: Optional[NewsAnalysisResult] = Field(
        None,
        description="News analysis result"
    )

    # Error handling
    error: Optional[str] = Field(None, description="Error message if failed")
    clarification_needed: Optional[ClarificationRequest] = Field(
        None,
        description="Clarification request if more info needed"
    )

    # Raw content for backward compatibility and debugging
    raw_content: Optional[str] = Field(
        None,
        description="Raw LLM response content"
    )
    raw_data: Optional[Dict[str, Any]] = Field(
        None,
        description="Raw structured data from response"
    )


# ============================================================================
# Synthesis Layer Schemas
# ============================================================================


class SynthesisInput(CamelModel):
    """Input for the synthesis layer."""

    symbol: str = Field(..., description="Stock symbol")
    market: str = Field(..., description="Market identifier")
    language: str = Field("en", description="Output language")
    agent_results: List[AgentAnalysisResult] = Field(
        ...,
        description="Results from individual agents"
    )
    user_context: Optional[str] = Field(
        None,
        description="Additional user context or question"
    )


class InvestmentRecommendation(CamelModel):
    """Final investment recommendation from synthesis."""

    action: ActionRecommendation = Field(
        ...,
        description="Overall recommended action"
    )
    confidence: AnalysisConfidence = Field(
        ...,
        description="Confidence in recommendation"
    )
    time_horizon: str = Field(
        "medium_term",
        description="Recommended time horizon"
    )
    risk_level: str = Field(
        "moderate",
        description="Risk level: 'low', 'moderate', 'high'"
    )
    target_price: Optional[float] = Field(
        None,
        description="Target price if applicable"
    )
    stop_loss: Optional[float] = Field(
        None,
        description="Suggested stop loss level"
    )


class SynthesisResult(CamelModel):
    """
    Final synthesized analysis result combining all agent outputs.

    This is the output of the synthesis layer that combines fundamental,
    technical, sentiment, and news analysis into a coherent recommendation.
    """

    symbol: str = Field(..., description="Stock symbol")
    market: str = Field(..., description="Market identifier")
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="Synthesis timestamp"
    )

    # Overall assessment
    recommendation: InvestmentRecommendation = Field(
        ...,
        description="Final investment recommendation"
    )

    # Aggregated insights
    key_insights: List[KeyInsight] = Field(
        default_factory=list,
        description="Top insights across all analyses"
    )
    strengths: List[str] = Field(
        default_factory=list,
        description="Key strengths identified"
    )
    weaknesses: List[str] = Field(
        default_factory=list,
        description="Key weaknesses identified"
    )
    risks: List[str] = Field(
        default_factory=list,
        description="Key risk factors"
    )
    catalysts: List[str] = Field(
        default_factory=list,
        description="Potential catalysts for price movement"
    )

    # Individual agent summaries
    fundamental_summary: Optional[str] = Field(
        None,
        description="Summary from fundamental analysis"
    )
    technical_summary: Optional[str] = Field(
        None,
        description="Summary from technical analysis"
    )
    sentiment_summary: Optional[str] = Field(
        None,
        description="Summary from sentiment analysis"
    )
    news_summary: Optional[str] = Field(
        None,
        description="Summary from news analysis"
    )

    # Overall narrative
    executive_summary: str = Field(
        ...,
        description="Executive summary of the complete analysis"
    )

    # Metadata
    agents_used: List[str] = Field(
        default_factory=list,
        description="List of agents that contributed"
    )
    total_tokens: int = Field(0, description="Total tokens used across all agents")
    total_latency_ms: int = Field(0, description="Total latency in milliseconds")
