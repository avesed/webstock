"""Skill: get backtest status and results.

Returns summarised metrics to the LLM instead of the full results blob
(equity_curve + trades can be 25K+ chars, exceeding the chat tool
serialisation budget).  The full data remains in the DB / REST API for
the frontend to render charts.

When ``include_details=true`` the skill also returns the most recent
trade records so the LLM can inspect individual transactions.
"""

import logging
from collections import defaultdict
from typing import Any

from app.services.backtest_service import BacktestManagementService
from app.skills.base import BaseSkill, SkillDefinition, SkillParameter, SkillResult

logger = logging.getLogger(__name__)

# Metric keys to extract verbatim from backtest.results
_METRIC_KEYS = [
    "total_return",
    "annual_return",
    "sharpe_ratio",
    "sortino_ratio",
    "max_drawdown",
    "max_drawdown_period",
    "annual_volatility",
    "calmar_ratio",
    "turnover_rate",
    "win_rate",
    "profit_loss_ratio",
    "total_trades",
    "trading_days",
    "duration_s",
]

# Max recent trades when include_details=true
_MAX_DETAIL_TRADES = 30


def _summarise_results(results: dict, *, include_details: bool = False) -> dict:
    """Extract LLM-friendly summary from the full results dict.

    All fields are **flat scalars or top-level lists** so that
    ``_smart_serialize`` in ``chat_adapter.py`` preserves every scalar
    and only binary-search-truncates lists.  Nested dicts would be
    collapsed to ``"{...}"`` — avoid them.
    """
    summary: dict[str, Any] = {}

    # Copy scalar metrics
    for key in _METRIC_KEYS:
        if key in results:
            summary[key] = results[key]

    # Strategy info — flatten date_range
    for key in ("strategy_type", "symbol_count"):
        if key in results:
            summary[key] = results[key]
    dr = results.get("date_range")
    if isinstance(dr, dict):
        summary["actual_start"] = dr.get("start")
        summary["actual_end"] = dr.get("end")

    # Flatten max_drawdown_period (currently a nested dict)
    mdd = summary.pop("max_drawdown_period", None)
    if isinstance(mdd, dict):
        summary["max_dd_start"] = mdd.get("start")
        summary["max_dd_end"] = mdd.get("end")

    # Equity curve: flatten to scalars
    equity_curve = results.get("equity_curve")
    if equity_curve and isinstance(equity_curve, list) and len(equity_curve) >= 2:
        values = [p["value"] for p in equity_curve if "value" in p]
        if values:
            summary["eq_points"] = len(equity_curve)
            summary["eq_start_date"] = equity_curve[0].get("date")
            summary["eq_start_value"] = values[0]
            summary["eq_end_date"] = equity_curve[-1].get("date")
            summary["eq_end_value"] = values[-1]
            summary["eq_min"] = round(min(values), 2)
            summary["eq_max"] = round(max(values), 2)

    # --- Trade analysis (all flat) ---
    trades = results.get("trades")
    if trades and isinstance(trades, list):
        symbol_counts: dict[str, int] = {}
        buy_count = sell_count = 0
        for t in trades:
            sym = t.get("symbol", "unknown")
            symbol_counts[sym] = symbol_counts.get(sym, 0) + 1
            direction = t.get("direction", "")
            if direction == "buy":
                buy_count += 1
            elif direction == "sell":
                sell_count += 1

        symbols_bought = sorted({
            t.get("symbol")
            for t in trades
            if t.get("direction") == "buy" and t.get("symbol")
        })

        summary["trade_buys"] = buy_count
        summary["trade_sells"] = sell_count
        summary["unique_symbols_traded"] = len(symbols_bought)
        # Compact string so it stays a scalar (no nested list)
        summary["symbols_bought"] = ", ".join(symbols_bought)

        # Per-symbol trade count as a compact string
        top_symbols = sorted(symbol_counts.items(), key=lambda x: -x[1])[:10]
        summary["per_symbol_trades"] = ", ".join(
            f"{s}:{c}" for s, c in top_symbols
        )

        # Group trades by date for rebalance rotation
        by_date: dict[str, list[str]] = defaultdict(list)
        for t in trades:
            dt = t.get("date", "")
            sym = t.get("symbol", "?")
            direction = t.get("direction", "?")
            by_date[dt].append(f"{direction[0]}:{sym}")  # b:HK1810, s:HK0700

        dates_sorted = sorted(by_date.keys())
        summary["rebalance_count"] = len(dates_sorted)

        # Rebalance sample as a top-level list of compact strings
        # e.g. ["2023-03-13 s:HK1810 b:HK0700", ...]
        if len(dates_sorted) <= 10:
            sample_dates = dates_sorted
        else:
            sample_dates = dates_sorted[:5] + dates_sorted[-5:]
        summary["rebalance_sample"] = [
            f"{d} {' '.join(by_date[d])}" for d in sample_dates
        ]

        # Optionally include recent trade records as a top-level list
        if include_details:
            recent = trades[-_MAX_DETAIL_TRADES:]
            summary["recent_trades"] = [
                f"{t.get('date')} {t.get('direction','?')} "
                f"{t.get('symbol','?')} x{t.get('shares',0):.2f} "
                f"@{t.get('price',0):.4f}"
                for t in recent
            ]

    return summary


class QlibGetBacktestSkill(BaseSkill):
    def definition(self) -> SkillDefinition:
        return SkillDefinition(
            name="qlib_get_backtest",
            description=(
                "Get the status and results of a Qlib backtest by ID. "
                "If the backtest is still running, returns current progress. "
                "If completed, returns performance metrics (annualized return, "
                "max drawdown, Sharpe ratio, etc.), a compact summary of "
                "the equity curve, and trade rotation details showing which "
                "symbols were selected at each rebalance. "
                "Set include_details=true to also get recent individual trade records."
            ),
            category="quantitative",
            parameters=[
                SkillParameter(
                    name="backtest_id",
                    type="string",
                    description="The backtest UUID returned by qlib_create_backtest",
                    required=True,
                ),
                SkillParameter(
                    name="include_details",
                    type="boolean",
                    description=(
                        "If true, also return the most recent trade records "
                        "(up to 30) with symbol, direction, shares, price, value"
                    ),
                    required=False,
                    default=False,
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> SkillResult:
        backtest_id = kwargs.get("backtest_id", "")
        include_details = bool(kwargs.get("include_details", False))
        user_id = kwargs.get("user_id")
        db = kwargs.get("db")

        if not backtest_id:
            return SkillResult(success=False, error="backtest_id is required")
        if user_id is None:
            return SkillResult(success=False, error="user_id is required (internal)")
        if db is None:
            return SkillResult(success=False, error="db session is required (internal)")

        try:
            backtest = await BacktestManagementService.get_backtest(
                db, user_id, backtest_id,
            )

            if backtest is None:
                return SkillResult(
                    success=False,
                    error=f"Backtest {backtest_id} not found",
                )

            data: dict[str, Any] = {
                "backtest_id": str(backtest.id),
                "name": backtest.name,
                "status": backtest.status,
                "progress": backtest.progress,
                "market": backtest.market,
                "symbols": backtest.symbols,
                "start_date": str(backtest.start_date),
                "end_date": str(backtest.end_date),
                "strategy_type": backtest.strategy_type,
            }

            if backtest.error_message:
                data["error"] = backtest.error_message

            if backtest.results and isinstance(backtest.results, dict):
                data.update(_summarise_results(
                    backtest.results,
                    include_details=include_details,
                ))

            return SkillResult(success=True, data=data)

        except Exception as e:
            logger.error("qlib_get_backtest unexpected error: %s", e)
            return SkillResult(success=False, error=f"Failed to get backtest: {e}")
