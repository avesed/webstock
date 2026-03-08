"""ML Agent system — LLM-guided training configuration.

Three agents form the pipeline:
1. Profiler  — analyzes data statistics (Python + 1 LLM call)
2. Strategist — generates training config (1 LLM call)
3. Evaluator  — decides deploy/retry/reject (1 LLM call)

Usage:
    from app.services.ml_agents import get_training_config
    cfg = await get_training_config(market, feature_df, symbols)
    # cfg is a MarketConfig — drop-in replacement
"""

import logging

import pandas as pd

from app.services.market_config import MarketConfig, get_market_config

logger = logging.getLogger(__name__)


async def get_training_config(
    market: str,
    feature_df: pd.DataFrame,
    symbols: list[str],
    recent_model_ics: list[float] | None = None,
) -> MarketConfig:
    """LLM-guided config generation with fallback to defaults.

    This is the primary entry point. On any LLM failure,
    falls back to the static MarketConfig defaults.

    Args:
        market: Market code (us, cn, hk).
        feature_df: Feature matrix from build_feature_matrix().
        symbols: List of stock symbols.
        recent_model_ics: Recent model IC values for trend detection.

    Returns:
        MarketConfig instance (either LLM-generated or default fallback).
    """
    try:
        from app.services.ml_agents.llm_client import MLAgentClient
        from app.services.ml_agents.profiler import profiler
        from app.services.ml_agents.strategist import strategist

        client = MLAgentClient()
        if not await client.is_available():
            logger.info("LLM not available, using default MarketConfig for %s", market)
            return get_market_config(market)

        profile = await profiler.analyze(feature_df, market, symbols, recent_model_ics)
        config = await strategist.generate(profile, market)

        logger.info(
            "LLM-generated config for %s: reasoning=%s",
            market,
            config.reasoning[:150],
        )
        return config.to_market_config()

    except Exception as e:
        logger.warning(
            "ML agents unavailable for %s, falling back to MarketConfig: %s",
            market,
            e,
        )
        return get_market_config(market)
