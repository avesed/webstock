"""Filter statistics service for monitoring news filtering effectiveness.

Tracks filter decisions and errors using Redis counters with daily granularity.
Provides statistics for admin dashboard and alerting.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from app.db.redis import get_redis

logger = logging.getLogger(__name__)

# Redis key patterns
KEY_PREFIX = "news:filter"
TTL_DAYS = 7  # Keep stats for 7 days


class FilterStatsService:
    """
    Service for tracking and querying news filter statistics.

    Uses Redis counters with daily granularity for efficient tracking.
    Supports multiple stat types for both initial and deep filtering stages.

    Stat types:
    - initial_useful: Initial filter marked as useful
    - initial_uncertain: Initial filter marked as uncertain
    - initial_skip: Initial filter marked as skip (not stored)
    - fine_keep: Deep filter decided to keep
    - fine_delete: Deep filter decided to delete
    - filter_error: Filter processing error
    - embedding_success: Embedding completed successfully
    - embedding_error: Embedding failed

    Token tracking:
    - initial_input_tokens: Input tokens for initial filter LLM calls
    - initial_output_tokens: Output tokens for initial filter LLM calls
    - deep_input_tokens: Input tokens for deep filter LLM calls
    - deep_output_tokens: Output tokens for deep filter LLM calls
    """

    STAT_TYPES = [
        "initial_useful",
        "initial_uncertain",
        "initial_skip",
        "fine_keep",
        "fine_delete",
        "filter_error",
        "embedding_success",
        "embedding_error",
    ]

    TOKEN_TYPES = [
        "initial_input_tokens",
        "initial_output_tokens",
        "deep_input_tokens",
        "deep_output_tokens",
    ]

    async def increment(self, stat_type: str, count: int = 1) -> None:
        """
        Increment a filter statistic counter.

        Args:
            stat_type: Type of statistic (see STAT_TYPES or TOKEN_TYPES)
            count: Amount to increment (default 1)
        """
        valid_types = self.STAT_TYPES + self.TOKEN_TYPES
        if stat_type not in valid_types:
            logger.warning(f"Unknown stat type: {stat_type}")
            return

        try:
            redis = await get_redis()
            date_str = datetime.now().strftime("%Y%m%d")
            key = f"{KEY_PREFIX}:{date_str}:{stat_type}"

            await redis.incrby(key, count)
            await redis.expire(key, TTL_DAYS * 86400)

        except Exception as e:
            logger.warning(f"Failed to increment filter stat {stat_type}: {e}")

    async def track_tokens(
        self,
        stage: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        """
        Track token usage for a filter stage.

        Args:
            stage: "initial" or "deep"
            input_tokens: Number of input tokens used
            output_tokens: Number of output tokens used
        """
        if stage not in ("initial", "deep"):
            logger.warning(f"Unknown filter stage: {stage}")
            return

        await self.increment(f"{stage}_input_tokens", input_tokens)
        await self.increment(f"{stage}_output_tokens", output_tokens)

    async def get_daily_stats(self, date: Optional[str] = None) -> Dict[str, int]:
        """
        Get all filter statistics for a specific date.

        Args:
            date: Date string in YYYYMMDD format (default: today)

        Returns:
            Dict mapping stat_type to count (includes both stats and tokens)
        """
        if date is None:
            date = datetime.now().strftime("%Y%m%d")

        all_types = self.STAT_TYPES + self.TOKEN_TYPES
        stats = {stat: 0 for stat in all_types}

        try:
            redis = await get_redis()

            for stat_type in all_types:
                key = f"{KEY_PREFIX}:{date}:{stat_type}"
                value = await redis.get(key)
                if value:
                    stats[stat_type] = int(value)

        except Exception as e:
            logger.warning(f"Failed to get daily stats for {date}: {e}")

        return stats

    async def get_stats_range(self, days: int = 7) -> Dict[str, Dict[str, int]]:
        """
        Get filter statistics for a date range.

        Args:
            days: Number of days to look back (including today)

        Returns:
            Dict mapping date (YYYYMMDD) to stats dict
        """
        result = {}
        today = datetime.now()

        for i in range(days):
            date = (today - timedelta(days=i)).strftime("%Y%m%d")
            result[date] = await self.get_daily_stats(date)

        return result

    async def get_summary_stats(self, days: int = 7) -> Dict[str, int]:
        """
        Get aggregated summary statistics over a date range.

        Args:
            days: Number of days to aggregate

        Returns:
            Dict with aggregated counts for each stat type (includes tokens)
        """
        range_stats = await self.get_stats_range(days)
        all_types = self.STAT_TYPES + self.TOKEN_TYPES
        summary = {stat: 0 for stat in all_types}

        for date_stats in range_stats.values():
            for stat_type, count in date_stats.items():
                if stat_type in summary:
                    summary[stat_type] += count

        return summary

    async def get_filter_rates(self, days: int = 7) -> Dict[str, float]:
        """
        Calculate filter effectiveness rates.

        Returns:
            Dict with calculated rates:
            - initial_skip_rate: Percentage skipped in initial filter
            - fine_delete_rate: Percentage deleted in deep filter
            - filter_error_rate: Percentage of filter errors
            - embedding_error_rate: Percentage of embedding errors
        """
        summary = await self.get_summary_stats(days)

        # Initial filter totals
        initial_total = (
            summary["initial_useful"]
            + summary["initial_uncertain"]
            + summary["initial_skip"]
        )

        # Deep filter totals
        fine_total = summary["fine_keep"] + summary["fine_delete"]

        # Embedding totals
        embedding_total = summary["embedding_success"] + summary["embedding_error"]

        # Calculate rates (avoid division by zero)
        rates = {
            "initial_skip_rate": (
                summary["initial_skip"] / initial_total if initial_total > 0 else 0.0
            ),
            "fine_delete_rate": (
                summary["fine_delete"] / fine_total if fine_total > 0 else 0.0
            ),
            "filter_error_rate": (
                summary["filter_error"] / (initial_total + fine_total)
                if (initial_total + fine_total) > 0
                else 0.0
            ),
            "embedding_error_rate": (
                summary["embedding_error"] / embedding_total if embedding_total > 0 else 0.0
            ),
        }

        return rates

    async def get_token_summary(self, days: int = 7) -> Dict[str, any]:
        """
        Get token usage summary with cost estimates.

        Args:
            days: Number of days to aggregate

        Returns:
            Dict with token counts and estimated costs
        """
        summary = await self.get_summary_stats(days)

        initial_input = summary.get("initial_input_tokens", 0)
        initial_output = summary.get("initial_output_tokens", 0)
        deep_input = summary.get("deep_input_tokens", 0)
        deep_output = summary.get("deep_output_tokens", 0)

        # Cost estimates for gpt-4o-mini (as of 2024)
        # Input: $0.15 / 1M tokens, Output: $0.60 / 1M tokens
        input_rate = 0.15 / 1_000_000
        output_rate = 0.60 / 1_000_000

        initial_cost = (initial_input * input_rate) + (initial_output * output_rate)
        deep_cost = (deep_input * input_rate) + (deep_output * output_rate)

        return {
            "initial_filter": {
                "input_tokens": initial_input,
                "output_tokens": initial_output,
                "total_tokens": initial_input + initial_output,
                "estimated_cost_usd": round(initial_cost, 4),
            },
            "deep_filter": {
                "input_tokens": deep_input,
                "output_tokens": deep_output,
                "total_tokens": deep_input + deep_output,
                "estimated_cost_usd": round(deep_cost, 4),
            },
            "total": {
                "input_tokens": initial_input + deep_input,
                "output_tokens": initial_output + deep_output,
                "total_tokens": initial_input + initial_output + deep_input + deep_output,
                "estimated_cost_usd": round(initial_cost + deep_cost, 4),
            },
            "days": days,
        }

    async def get_comprehensive_stats(self, days: int = 7) -> Dict[str, any]:
        """
        Get comprehensive filter statistics for admin dashboard.

        Returns:
            Dict with counts, rates, tokens, and alerts
        """
        summary = await self.get_summary_stats(days)
        rates = await self.get_filter_rates(days)
        tokens = await self.get_token_summary(days)
        alerts = await self.check_thresholds()

        # Calculate totals
        initial_total = (
            summary["initial_useful"]
            + summary["initial_uncertain"]
            + summary["initial_skip"]
        )
        deep_total = summary["fine_keep"] + summary["fine_delete"]

        return {
            "period_days": days,
            "counts": {
                "initial_filter": {
                    "useful": summary["initial_useful"],
                    "uncertain": summary["initial_uncertain"],
                    "skip": summary["initial_skip"],
                    "total": initial_total,
                },
                "deep_filter": {
                    "keep": summary["fine_keep"],
                    "delete": summary["fine_delete"],
                    "total": deep_total,
                },
                "errors": {
                    "filter_error": summary["filter_error"],
                    "embedding_error": summary["embedding_error"],
                },
                "embedding": {
                    "success": summary["embedding_success"],
                    "error": summary["embedding_error"],
                },
            },
            "rates": {
                "initial_skip_rate": round(rates["initial_skip_rate"] * 100, 1),
                "initial_pass_rate": round((1 - rates["initial_skip_rate"]) * 100, 1),
                "deep_keep_rate": round((1 - rates["fine_delete_rate"]) * 100, 1),
                "deep_delete_rate": round(rates["fine_delete_rate"] * 100, 1),
                "filter_error_rate": round(rates["filter_error_rate"] * 100, 2),
                "embedding_error_rate": round(rates["embedding_error_rate"] * 100, 2),
            },
            "tokens": tokens,
            "alerts": alerts,
        }

    async def check_thresholds(self) -> List[Dict[str, str]]:
        """
        Check if any rates exceed warning/critical thresholds.

        Returns:
            List of alert dicts with keys: stat, rate, level, message
        """
        rates = await self.get_filter_rates(days=1)  # Check last 24h
        alerts = []

        # Threshold definitions: (stat_key, warning, critical)
        thresholds = [
            ("initial_skip_rate", 0.70, 0.85),
            ("fine_delete_rate", 0.60, 0.80),
            ("filter_error_rate", 0.05, 0.15),
            ("embedding_error_rate", 0.10, 0.25),
        ]

        for stat_key, warn_threshold, crit_threshold in thresholds:
            rate = rates.get(stat_key, 0.0)

            if rate >= crit_threshold:
                alerts.append({
                    "stat": stat_key,
                    "rate": f"{rate:.1%}",
                    "level": "critical",
                    "message": f"{stat_key} is critically high at {rate:.1%} (threshold: {crit_threshold:.0%})",
                })
            elif rate >= warn_threshold:
                alerts.append({
                    "stat": stat_key,
                    "rate": f"{rate:.1%}",
                    "level": "warning",
                    "message": f"{stat_key} is elevated at {rate:.1%} (threshold: {warn_threshold:.0%})",
                })

        return alerts


# Singleton instance
_service: Optional[FilterStatsService] = None


def get_filter_stats_service() -> FilterStatsService:
    """Get singleton instance of FilterStatsService."""
    global _service
    if _service is None:
        _service = FilterStatsService()
    return _service
