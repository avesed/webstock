"""Pydantic schemas for AI analysis endpoints."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.base import CamelModel


class AgentTypeEnum(str, Enum):
    """Analysis agent types."""

    FUNDAMENTAL = "fundamental"
    TECHNICAL = "technical"
    SENTIMENT = "sentiment"


class ValuationAssessment(str, Enum):
    """Valuation assessment levels."""

    UNDERVALUED = "undervalued"
    FAIRLY_VALUED = "fairly_valued"
    OVERVALUED = "overvalued"


class TrendDirection(str, Enum):
    """Trend direction."""

    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"


class SentimentLabel(str, Enum):
    """Sentiment labels."""

    VERY_BEARISH = "very_bearish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    BULLISH = "bullish"
    VERY_BULLISH = "very_bullish"


class ConfidenceLevel(str, Enum):
    """Confidence levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# Fundamental Analysis Schemas
class ValuationResult(CamelModel):
    """Valuation assessment result."""

    assessment: Optional[ValuationAssessment] = None
    confidence: Optional[ConfidenceLevel] = None
    reasoning: Optional[str] = None


class KeyMetrics(CamelModel):
    """Key financial metrics analysis."""

    pe_analysis: Optional[str] = None
    growth_analysis: Optional[str] = None
    profitability_analysis: Optional[str] = None
    balance_sheet_analysis: Optional[str] = None


class FundamentalRecommendation(CamelModel):
    """Fundamental analysis recommendation."""

    action: Optional[str] = None  # buy, hold, sell, avoid
    rationale: Optional[str] = None


class FundamentalStructuredData(CamelModel):
    """Structured data from fundamental analysis."""

    summary: Optional[str] = None
    valuation: Optional[ValuationResult] = None
    key_metrics: Optional[KeyMetrics] = None
    strengths: Optional[List[str]] = None
    weaknesses: Optional[List[str]] = None
    risks: Optional[List[str]] = None
    recommendation: Optional[FundamentalRecommendation] = None


# Technical Analysis Schemas
class TrendAnalysis(CamelModel):
    """Trend analysis at different timeframes."""

    short_term: Optional[TrendDirection] = None
    medium_term: Optional[TrendDirection] = None
    long_term: Optional[TrendDirection] = None
    description: Optional[str] = None


class KeyLevels(CamelModel):
    """Support and resistance levels."""

    support: Optional[List[str]] = None
    resistance: Optional[List[str]] = None


class IndicatorAnalysis(CamelModel):
    """Technical indicator analysis."""

    moving_averages: Optional[str] = None
    rsi: Optional[str] = None
    macd: Optional[str] = None
    volume: Optional[str] = None


class TechnicalSignals(CamelModel):
    """Technical signals analysis."""

    bullish: Optional[List[str]] = None
    bearish: Optional[List[str]] = None


class TechnicalRecommendation(CamelModel):
    """Technical analysis recommendation."""

    bias: Optional[TrendDirection] = None
    entry_zone: Optional[str] = None
    stop_loss: Optional[str] = None
    targets: Optional[List[str]] = None
    rationale: Optional[str] = None


class TechnicalStructuredData(CamelModel):
    """Structured data from technical analysis."""

    summary: Optional[str] = None
    trend: Optional[TrendAnalysis] = None
    key_levels: Optional[KeyLevels] = None
    indicators: Optional[IndicatorAnalysis] = None
    patterns: Optional[List[str]] = None
    signals: Optional[TechnicalSignals] = None
    recommendation: Optional[TechnicalRecommendation] = None


# Sentiment Analysis Schemas
class OverallSentiment(CamelModel):
    """Overall sentiment assessment."""

    score: Optional[float] = Field(None, ge=-100, le=100)
    label: Optional[SentimentLabel] = None
    confidence: Optional[ConfidenceLevel] = None


class MomentumAssessment(CamelModel):
    """Price momentum assessment."""

    assessment: Optional[str] = None  # strong_downtrend, downtrend, neutral, uptrend, strong_uptrend
    reasoning: Optional[str] = None


class VolumeSentiment(CamelModel):
    """Volume-based sentiment."""

    assessment: Optional[str] = None  # distribution, neutral, accumulation
    reasoning: Optional[str] = None


class MarketContext(CamelModel):
    """Broader market context."""

    sector_trend: Optional[str] = None
    broader_market: Optional[str] = None
    relative_strength: Optional[str] = None


class NewsSentiment(CamelModel):
    """News-based sentiment."""

    score: Optional[float] = Field(None, ge=-100, le=100)
    key_themes: Optional[List[str]] = None
    impact_assessment: Optional[str] = None


class Catalysts(CamelModel):
    """Potential catalysts."""

    bullish: Optional[List[str]] = None
    bearish: Optional[List[str]] = None


class SentimentRecommendation(CamelModel):
    """Sentiment analysis recommendation."""

    sentiment_bias: Optional[TrendDirection] = None
    timing: Optional[str] = None
    rationale: Optional[str] = None


class SentimentStructuredData(CamelModel):
    """Structured data from sentiment analysis."""

    summary: Optional[str] = None
    overall_sentiment: Optional[OverallSentiment] = None
    price_momentum: Optional[MomentumAssessment] = None
    volume_sentiment: Optional[VolumeSentiment] = None
    market_context: Optional[MarketContext] = None
    news_sentiment: Optional[NewsSentiment] = None
    risk_factors: Optional[List[str]] = None
    catalysts: Optional[Catalysts] = None
    recommendation: Optional[SentimentRecommendation] = None


# Agent Result Schemas
class AgentResultResponse(CamelModel):
    """Response from a single agent analysis."""

    agent_type: AgentTypeEnum
    symbol: str
    market: str
    success: bool
    content: Optional[str] = None
    structured_data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    tokens_used: int = 0
    latency_ms: int = 0
    timestamp: float


class AnalysisSummary(CamelModel):
    """Summary of analysis results."""

    successful_agents: List[str]
    failed_agents: List[str]
    recommendations: Dict[str, Any]


class FullAnalysisResponse(CamelModel):
    """Response from full multi-agent analysis."""

    symbol: str
    market: str
    results: Dict[str, AgentResultResponse]
    total_tokens: int
    total_latency_ms: int
    timestamp: float
    summary: AnalysisSummary


class SingleAnalysisResponse(CamelModel):
    """Response from single agent analysis."""

    symbol: str
    market: str
    agent_type: AgentTypeEnum
    success: bool
    content: Optional[str] = None
    structured_data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    tokens_used: int = 0
    latency_ms: int = 0
    timestamp: float


# SSE Stream Event Schemas
class StreamStartEvent(CamelModel):
    """SSE event for stream start."""

    type: str = "start"
    symbol: str
    market: str
    agents: List[str]
    timestamp: float


class StreamAgentStartEvent(CamelModel):
    """SSE event for agent start."""

    type: str = "agent_start"
    agent: str
    timestamp: float


class StreamAgentChunkEvent(CamelModel):
    """SSE event for agent content chunk."""

    type: str = "agent_chunk"
    agent: str
    content: str
    timestamp: float


class StreamAgentCompleteEvent(CamelModel):
    """SSE event for agent completion."""

    type: str = "agent_complete"
    agent: str
    structured_data: Optional[Dict[str, Any]] = None
    timestamp: float


class StreamAgentErrorEvent(CamelModel):
    """SSE event for agent error."""

    type: str = "agent_error"
    agent: str
    error: str
    timestamp: float


class StreamCompleteEvent(CamelModel):
    """SSE event for stream completion."""

    type: str = "complete"
    symbol: str
    market: str
    completed_agents: List[str]
    total_latency_ms: int
    timestamp: float


class AnalysisErrorResponse(CamelModel):
    """Error response for analysis endpoints."""

    detail: str
    code: Optional[str] = None
    agent: Optional[str] = None
