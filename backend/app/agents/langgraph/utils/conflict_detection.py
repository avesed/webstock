"""Conflict detection utilities for multi-agent analysis.

This module provides functions to detect conflicts, inconsistencies,
and data quality issues across analysis results from multiple agents.
"""

import logging
from typing import Any, Dict, List, Optional

from app.schemas.agent_analysis import (
    ActionRecommendation,
    AgentAnalysisResult,
    AnalysisConfidence,
    SentimentLevel,
    TrendDirection,
    ValuationAssessment,
)

logger = logging.getLogger(__name__)

# Confidence score mapping for AnalysisConfidence enum
CONFIDENCE_SCORES = {
    AnalysisConfidence.HIGH: 0.9,
    AnalysisConfidence.MEDIUM: 0.7,
    AnalysisConfidence.LOW: 0.5,
}

# Sentiment level ordering (more positive = higher value)
SENTIMENT_ORDER = {
    SentimentLevel.VERY_NEGATIVE: 0,
    SentimentLevel.NEGATIVE: 1,
    SentimentLevel.NEUTRAL: 2,
    SentimentLevel.POSITIVE: 3,
    SentimentLevel.VERY_POSITIVE: 4,
}

# Action recommendation ordering (more bullish = higher value)
ACTION_ORDER = {
    ActionRecommendation.STRONG_SELL: 0,
    ActionRecommendation.SELL: 1,
    ActionRecommendation.AVOID: 1,
    ActionRecommendation.HOLD: 2,
    ActionRecommendation.BUY: 3,
    ActionRecommendation.STRONG_BUY: 4,
}


def _get_action_score(result: AgentAnalysisResult) -> Optional[int]:
    """Get the action score from a result."""
    if result.fundamental and result.fundamental.action:
        return ACTION_ORDER.get(result.fundamental.action)
    if result.technical and result.technical.action:
        return ACTION_ORDER.get(result.technical.action)
    return None


def _get_sentiment_score(result: AgentAnalysisResult) -> Optional[int]:
    """Get the sentiment score from a result."""
    if result.sentiment and result.sentiment.overall_sentiment:
        return SENTIMENT_ORDER.get(result.sentiment.overall_sentiment)
    if result.news and result.news.overall_sentiment:
        return SENTIMENT_ORDER.get(result.news.overall_sentiment)
    return None


def _get_trend_direction(result: AgentAnalysisResult) -> Optional[TrendDirection]:
    """Get the trend direction from a result."""
    if result.technical:
        return result.technical.trend
    if result.sentiment:
        return result.sentiment.sentiment_trend
    if result.news:
        return result.news.news_trend
    return None


def _get_confidence_score(result: AgentAnalysisResult) -> float:
    """
    Get a numeric confidence score from a result.

    Returns a value between 0 and 1.
    """
    # Check for explicit confidence in structured results
    if result.fundamental:
        conf = result.fundamental.valuation_confidence
        return CONFIDENCE_SCORES.get(conf, 0.7)
    if result.technical:
        conf = result.technical.trend_strength
        return CONFIDENCE_SCORES.get(conf, 0.7)
    if result.sentiment:
        conf = result.sentiment.confidence
        return CONFIDENCE_SCORES.get(conf, 0.7)
    if result.news:
        conf = result.news.confidence
        return CONFIDENCE_SCORES.get(conf, 0.7)

    # Default medium confidence if no structured data
    return 0.7


def detect_conflicts(results: List[AgentAnalysisResult]) -> List[Dict[str, Any]]:
    """
    Detect conflicts between analysis results.

    Conflicts occur when:
    - Fundamental and technical have opposing actions (e.g., buy vs sell)
    - Technical trend and sentiment are strongly opposed
    - News sentiment contradicts technical signals

    Args:
        results: List of AgentAnalysisResult from different agents

    Returns:
        List of conflict descriptions with agent pairs and details
    """
    conflicts = []

    # Build lookup by agent type
    by_type = {}
    for r in results:
        if r.success:
            by_type[r.agent_type] = r

    # Check fundamental vs technical action conflict
    fundamental = by_type.get("fundamental")
    technical = by_type.get("technical")

    if fundamental and technical:
        fund_action = _get_action_score(fundamental)
        tech_action = _get_action_score(technical)

        if fund_action is not None and tech_action is not None:
            # Strong conflict: one says buy, other says sell
            if abs(fund_action - tech_action) >= 2:
                fund_action_name = fundamental.fundamental.action.value if fundamental.fundamental else "unknown"
                tech_action_name = technical.technical.action.value if technical.technical else "unknown"

                conflicts.append({
                    "agent1": "fundamental",
                    "agent2": "technical",
                    "type": "action_conflict",
                    "description": f"Fundamental suggests {fund_action_name} but technical suggests {tech_action_name}",
                    "severity": "high" if abs(fund_action - tech_action) >= 3 else "medium",
                })

    # Check technical trend vs sentiment conflict
    sentiment = by_type.get("sentiment")

    if technical and sentiment:
        tech_trend = _get_trend_direction(technical)
        sent_sentiment = _get_sentiment_score(sentiment)

        if tech_trend and sent_sentiment is not None:
            # Bullish technical but negative sentiment
            if tech_trend == TrendDirection.BULLISH and sent_sentiment <= 1:
                conflicts.append({
                    "agent1": "technical",
                    "agent2": "sentiment",
                    "type": "trend_sentiment_conflict",
                    "description": "Technical analysis shows bullish trend but market sentiment is negative",
                    "severity": "medium",
                })
            # Bearish technical but positive sentiment
            elif tech_trend == TrendDirection.BEARISH and sent_sentiment >= 3:
                conflicts.append({
                    "agent1": "technical",
                    "agent2": "sentiment",
                    "type": "trend_sentiment_conflict",
                    "description": "Technical analysis shows bearish trend but market sentiment is positive",
                    "severity": "medium",
                })

    # Check news sentiment vs other signals
    news = by_type.get("news")

    if news and technical:
        news_sentiment = _get_sentiment_score(news)
        tech_action = _get_action_score(technical)

        if news_sentiment is not None and tech_action is not None:
            # Very negative news but bullish technical
            if news_sentiment <= 1 and tech_action >= 3:
                conflicts.append({
                    "agent1": "news",
                    "agent2": "technical",
                    "type": "news_technical_conflict",
                    "description": "Recent news is very negative but technical indicators suggest buying",
                    "severity": "high",
                })
            # Very positive news but bearish technical
            elif news_sentiment >= 3 and tech_action <= 1:
                conflicts.append({
                    "agent1": "news",
                    "agent2": "technical",
                    "type": "news_technical_conflict",
                    "description": "Recent news is positive but technical indicators suggest selling",
                    "severity": "medium",
                })

    if conflicts:
        logger.info(f"Detected {len(conflicts)} conflicts between agents")

    return conflicts


def detect_low_confidence_results(
    results: List[AgentAnalysisResult],
    threshold: float = 0.6,
) -> List[AgentAnalysisResult]:
    """
    Detect results with low confidence.

    Args:
        results: List of analysis results
        threshold: Confidence threshold (0-1), results below this are flagged

    Returns:
        List of results with confidence below threshold
    """
    low_confidence = []

    for result in results:
        if not result.success:
            continue

        confidence = _get_confidence_score(result)
        if confidence < threshold:
            low_confidence.append(result)
            logger.debug(
                f"Low confidence detected for {result.agent_type}: {confidence:.2f}"
            )

    return low_confidence


def detect_missing_critical_data(
    results: List[AgentAnalysisResult],
) -> List[str]:
    """
    Detect missing critical data points in analysis results.

    Args:
        results: List of analysis results

    Returns:
        List of missing critical data descriptions
    """
    missing = []

    # Build lookup by agent type
    by_type = {}
    for r in results:
        if r.success:
            by_type[r.agent_type] = r

    # Check fundamental data
    fundamental = by_type.get("fundamental")
    if fundamental:
        if fundamental.fundamental:
            if not fundamental.fundamental.metrics:
                missing.append("Fundamental metrics (P/E, P/B, etc.) not available")
        elif not fundamental.raw_data:
            missing.append("Fundamental analysis produced no structured data")

    # Check technical data
    technical = by_type.get("technical")
    if technical:
        if technical.technical:
            if not technical.technical.indicators:
                missing.append("Technical indicators (RSI, MACD, etc.) not calculated")
            if not technical.technical.support_levels and not technical.technical.resistance_levels:
                missing.append("Support/resistance levels not identified")
        elif not technical.raw_data:
            missing.append("Technical analysis produced no structured data")

    # Check sentiment data
    sentiment = by_type.get("sentiment")
    if sentiment:
        if sentiment.sentiment:
            if not sentiment.sentiment.sources:
                missing.append("Sentiment sources breakdown not available")
        elif not sentiment.raw_data:
            missing.append("Sentiment analysis produced no structured data")

    # Check news data
    news = by_type.get("news")
    if news:
        if news.news:
            if not news.news.top_news:
                missing.append("No news articles found for analysis")
        elif not news.raw_data:
            missing.append("News analysis produced no structured data")

    # Check for completely missing agents
    for agent_type in ["fundamental", "technical", "sentiment", "news"]:
        if agent_type not in by_type:
            missing.append(f"{agent_type.capitalize()} analysis not available")

    if missing:
        logger.info(f"Detected {len(missing)} missing critical data points")

    return missing


def get_consensus_action(results: List[AgentAnalysisResult]) -> Optional[ActionRecommendation]:
    """
    Determine the consensus action from all results.

    Uses a simple voting mechanism weighted by confidence.

    Args:
        results: List of analysis results

    Returns:
        Consensus action recommendation or None if no consensus
    """
    votes = {
        "bullish": 0.0,
        "bearish": 0.0,
        "neutral": 0.0,
    }

    for result in results:
        if not result.success:
            continue

        action_score = _get_action_score(result)
        confidence = _get_confidence_score(result)

        if action_score is not None:
            if action_score >= 3:
                votes["bullish"] += confidence
            elif action_score <= 1:
                votes["bearish"] += confidence
            else:
                votes["neutral"] += confidence

    # Determine winner
    max_votes = max(votes.values()) if votes else 0
    if max_votes == 0:
        return None

    # Need at least 60% consensus
    total_votes = sum(votes.values())
    if max_votes / total_votes < 0.6:
        return ActionRecommendation.HOLD  # No clear consensus

    if votes["bullish"] == max_votes:
        return ActionRecommendation.BUY
    elif votes["bearish"] == max_votes:
        return ActionRecommendation.SELL
    else:
        return ActionRecommendation.HOLD


def summarize_conflicts(conflicts: List[Dict[str, Any]], language: str = "en") -> str:
    """
    Generate a human-readable summary of conflicts.

    Args:
        conflicts: List of conflict dictionaries
        language: Output language ("en" or "zh")

    Returns:
        Formatted conflict summary string
    """
    if not conflicts:
        if language == "zh":
            return "各分析代理的结论基本一致。"
        return "Analysis agents are largely in agreement."

    lines = []
    if language == "zh":
        lines.append("检测到以下分析分歧：")
        for i, conflict in enumerate(conflicts, 1):
            severity = {
                "high": "严重",
                "medium": "中等",
                "low": "轻微",
            }.get(conflict.get("severity", "medium"), "中等")
            lines.append(f"{i}. [{severity}] {conflict.get('description', '')}")
    else:
        lines.append("The following analysis conflicts were detected:")
        for i, conflict in enumerate(conflicts, 1):
            severity = conflict.get("severity", "medium").capitalize()
            lines.append(f"{i}. [{severity}] {conflict.get('description', '')}")

    return "\n".join(lines)
