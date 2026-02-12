"""Portfolio optimization service using PyPortfolioOpt.

Runs in the main backend process (not qlib-service) since it's lightweight
and stateless. Uses CanonicalCache daily close prices directly.

All computation is offloaded to a thread via asyncio.to_thread() to avoid
blocking the event loop.
"""

import asyncio
import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Limit concurrent price fetches to avoid overwhelming the cache/provider
_FETCH_CONCURRENCY = 10


class PortfolioOptimizationError(Exception):
    """Raised when optimization fails."""
    pass


class PortfolioOptimizationService:
    """PyPortfolioOpt wrapper for portfolio optimization."""

    @staticmethod
    async def _get_price_matrix(
        symbols: List[str],
        lookback_days: int = 252,
    ) -> pd.DataFrame:
        """Fetch daily close prices from CanonicalCache for all symbols.

        Uses asyncio.gather with a semaphore for parallel fetching.
        Returns a DataFrame with dates as index and symbols as columns.
        """
        from app.services.canonical_cache_service import get_canonical_cache_service

        end_date = date.today()
        start_date = end_date - timedelta(days=int(lookback_days * 1.5))

        cache = await get_canonical_cache_service()
        semaphore = asyncio.Semaphore(_FETCH_CONCURRENCY)

        async def _fetch_one(symbol: str) -> Tuple[str, Optional[pd.Series]]:
            async with semaphore:
                try:
                    bars = await cache.get_history(
                        symbol=symbol,
                        interval="1d",
                        period_days=int(lookback_days * 1.5),
                        start=str(start_date),
                        end=str(end_date),
                    )
                    if bars and len(bars) > 0:
                        df = pd.DataFrame(bars)
                        if "date" in df.columns and "close" in df.columns:
                            df["date"] = pd.to_datetime(df["date"])
                            df = df.set_index("date").sort_index()
                            return symbol, df["close"]
                except Exception as e:
                    logger.warning("Failed to fetch price data for %s: %s", symbol, e)
                return symbol, None

        results = await asyncio.gather(*[_fetch_one(s) for s in symbols])
        price_data = {sym: series for sym, series in results if series is not None}

        if len(price_data) < 2:
            raise PortfolioOptimizationError(
                f"Need at least 2 symbols with price data, got {len(price_data)}"
            )

        prices = pd.DataFrame(price_data)
        prices = prices.dropna(how="all").ffill().dropna()

        if len(prices.columns) < 2:
            raise PortfolioOptimizationError(
                f"Insufficient symbols with valid data after cleaning: "
                f"{list(prices.columns)}. Need at least 2."
            )

        if len(prices) < 30:
            raise PortfolioOptimizationError(
                f"Insufficient price data: {len(prices)} days (need at least 30)"
            )

        # Trim to requested lookback
        if len(prices) > lookback_days:
            prices = prices.iloc[-lookback_days:]

        return prices

    @staticmethod
    def _optimize_sync(
        prices: pd.DataFrame,
        method: str,
        constraints: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Synchronous optimization (runs in thread).

        Methods: max_sharpe, min_volatility, risk_parity, efficient_return
        """
        from pypfopt import (
            EfficientFrontier,
            expected_returns,
            risk_models,
        )

        constraints = constraints or {}

        # Compute expected returns and covariance
        mu = expected_returns.mean_historical_return(prices)
        S = risk_models.sample_cov(prices)

        # Weight bounds (validated by schema)
        weight_bounds = (
            constraints.get("min_weight", 0.0),
            constraints.get("max_weight", 1.0),
        )
        risk_free_rate = constraints.get("risk_free_rate", 0.02)

        if method == "max_sharpe":
            ef = EfficientFrontier(mu, S, weight_bounds=weight_bounds)
            ef.max_sharpe(risk_free_rate=risk_free_rate)
        elif method == "min_volatility":
            ef = EfficientFrontier(mu, S, weight_bounds=weight_bounds)
            ef.min_volatility()
        elif method == "efficient_return":
            target_return = constraints.get("target_return", 0.1)
            ef = EfficientFrontier(mu, S, weight_bounds=weight_bounds)
            ef.efficient_return(target_return=target_return)
        elif method == "risk_parity":
            from pypfopt import HRPOpt
            hrp = HRPOpt(expected_returns.returns_from_prices(prices))
            hrp.optimize()
            weights = hrp.clean_weights()
            perf = hrp.portfolio_performance(
                verbose=False, risk_free_rate=risk_free_rate,
            )
            return PortfolioOptimizationService._build_result(weights, perf, method)
        else:
            raise PortfolioOptimizationError(f"Unknown method: {method}")

        weights = ef.clean_weights()
        perf = ef.portfolio_performance(verbose=False)

        return PortfolioOptimizationService._build_result(weights, perf, method)

    @staticmethod
    def _build_result(
        weights: Dict[str, float],
        perf: tuple,
        method: str,
    ) -> Dict[str, Any]:
        """Validate and build optimization result dict.

        Raises PortfolioOptimizationError if metrics are NaN/Inf
        (can happen with singular covariance matrices).
        """
        if not all(np.isfinite(v) for v in perf):
            raise PortfolioOptimizationError(
                "Optimization produced invalid metrics (NaN/Inf). "
                "This may indicate insufficient data variance or "
                "numerical instability."
            )

        filtered_weights = {
            k: round(v, 6) for k, v in weights.items() if v > 1e-6
        }
        if len(filtered_weights) == 0:
            raise PortfolioOptimizationError(
                "Optimization resulted in no meaningful allocations. "
                "This may indicate incompatible constraints or data issues."
            )

        return {
            "weights": filtered_weights,
            "expected_return": round(float(perf[0]), 6),
            "annual_volatility": round(float(perf[1]), 6),
            "sharpe_ratio": round(float(perf[2]), 4),
            "method": method,
        }

    @staticmethod
    def _efficient_frontier_sync(
        prices: pd.DataFrame,
        n_points: int = 20,
        constraints: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Compute efficient frontier points (runs in thread)."""
        from pypfopt import EfficientFrontier, expected_returns, risk_models

        constraints = constraints or {}
        mu = expected_returns.mean_historical_return(prices)
        S = risk_models.sample_cov(prices)

        weight_bounds = (
            constraints.get("min_weight", 0.0),
            constraints.get("max_weight", 1.0),
        )

        # Find min feasible return (min-volatility portfolio)
        ef_min = EfficientFrontier(mu, S, weight_bounds=weight_bounds)
        ef_min.min_volatility()
        min_ret = ef_min.portfolio_performance(verbose=False)[0]

        # Upper bound: use the max individual asset expected return
        # (max_sharpe gives a return below this, so using mu.max() is more correct)
        max_ret = float(mu.max())
        if max_ret <= min_ret:
            max_ret = min_ret + 0.05

        # Generate frontier points
        target_returns = np.linspace(min_ret, max_ret, n_points)
        frontier: List[Dict[str, Any]] = []

        for target in target_returns:
            try:
                ef = EfficientFrontier(mu, S, weight_bounds=weight_bounds)
                ef.efficient_return(float(target))
                perf = ef.portfolio_performance(verbose=False)
                frontier.append({
                    "expected_return": round(perf[0], 6),
                    "volatility": round(perf[1], 6),
                    "sharpe_ratio": round(perf[2], 4),
                })
            except Exception as e:
                logger.debug("Frontier point at target=%.4f infeasible: %s", target, e)
                continue

        return frontier

    @staticmethod
    def _risk_decomposition_sync(
        prices: pd.DataFrame,
        weights: Dict[str, float],
    ) -> Dict[str, Any]:
        """Compute risk contribution per asset (runs in thread)."""
        from pypfopt import risk_models

        S = risk_models.sample_cov(prices)

        # Align weights with price columns
        symbols = list(prices.columns)
        w = np.array([weights.get(s, 0.0) for s in symbols])

        # Portfolio variance
        port_var = float(w @ S.values @ w)
        port_vol = float(np.sqrt(port_var))

        # Marginal risk contribution
        marginal = S.values @ w
        risk_contribution = w * marginal / port_vol if port_vol > 0 else w * 0

        contributions = {}
        for i, symbol in enumerate(symbols):
            if w[i] > 1e-6:
                contributions[symbol] = {
                    "weight": round(float(w[i]), 6),
                    "risk_contribution": round(float(risk_contribution[i]), 6),
                    "risk_pct": round(float(risk_contribution[i] / port_vol * 100), 2) if port_vol > 0 else 0,
                }

        return {
            "portfolio_volatility": round(port_vol, 6),
            "contributions": contributions,
        }

    @classmethod
    async def optimize(
        cls,
        symbols: List[str],
        method: str = "max_sharpe",
        constraints: Optional[Dict[str, Any]] = None,
        lookback_days: int = 252,
    ) -> Dict[str, Any]:
        """Run portfolio optimization."""
        logger.info("optimize: symbols=%d method=%s lookback=%d", len(symbols), method, lookback_days)
        prices = await cls._get_price_matrix(symbols, lookback_days)
        result = await asyncio.to_thread(
            cls._optimize_sync, prices, method, constraints,
        )
        result["symbols"] = list(prices.columns)
        result["data_days"] = len(prices)
        logger.info("optimize complete: %d symbols, sharpe=%.3f", len(result["symbols"]), result["sharpe_ratio"])
        return result

    @classmethod
    async def efficient_frontier(
        cls,
        symbols: List[str],
        n_points: int = 20,
        constraints: Optional[Dict[str, Any]] = None,
        lookback_days: int = 252,
    ) -> Dict[str, Any]:
        """Compute efficient frontier."""
        logger.info("efficient_frontier: symbols=%d n_points=%d", len(symbols), n_points)
        prices = await cls._get_price_matrix(symbols, lookback_days)
        frontier = await asyncio.to_thread(
            cls._efficient_frontier_sync, prices, n_points, constraints,
        )
        return {
            "symbols": list(prices.columns),
            "data_days": len(prices),
            "frontier": frontier,
        }

    @classmethod
    async def risk_decomposition(
        cls,
        symbols: List[str],
        weights: Dict[str, float],
        lookback_days: int = 252,
    ) -> Dict[str, Any]:
        """Compute risk decomposition."""
        logger.info("risk_decomposition: symbols=%d", len(symbols))
        prices = await cls._get_price_matrix(symbols, lookback_days)
        result = await asyncio.to_thread(
            cls._risk_decomposition_sync, prices, weights,
        )
        result["symbols"] = list(prices.columns)
        result["data_days"] = len(prices)
        return result
