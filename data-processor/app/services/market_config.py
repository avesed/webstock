"""Per-market ML configuration — single source of truth.

All market-specific behavior for prediction training, feature engineering,
and data quality is centralized here. Adding a new market-level feature
requires only a change in this file — no scattered ``if market == "..."``
checks in business logic.

Design principles:
- ``frozen=True`` prevents accidental mutation.
- ``lgb_overrides`` layered on top of ``_BASE_LGB_PARAMS`` in prediction_service.
- ``ffill_limit`` passed to fundamental_service to cap forward-fill age.
"""

import dataclasses
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MarketConfig:
    """All per-market ML behavior configuration.

    Training mode
    -------------
    use_temporal_sort : bool
        True  → symbol-date sort (CN/HK legacy mode — temporal momentum signal).
        False → date-symbol sort (US cross-sectional mode — relative ranking).

    Label engineering
    -----------------
    use_sector_neutral_labels : bool
        True  → subtract sector mean return from forward returns (US only).
        False → use raw forward returns.
        CN/HK: sector groups have only 3-10 stocks → sector mean is noise,
        not a meaningful industry benchmark. Explicitly disabled.
    use_balanced_quintiles : bool
        True  → rank(method="first") + qcut for uniform 5-bin distribution (US).
        False → qcut(duplicates="drop") — sparser labels work with CN/HK legacy mode.

    Feature engineering
    -------------------
    use_sector_rank : bool
        True  → rank valuation features within sectors (US only).
        False → cross-sectional rank only.
        Same small-group reasoning as use_sector_neutral_labels.
    use_interactions : bool
        True  → compute cross-feature interaction features (US only).
        CN/HK: legacy training mode + small universes make interactions harmful.
    nan_threshold : float
        Drop features with NaN rate above this threshold.
        US=0.75: prevents "has-data?" spurious splits from sparse features.
        CN/HK=0.90: sparser fundamental coverage, need to keep more features.
    ffill_limit : int
        Forward-fill limit (calendar days) for fundamental data.
        CN=90: quarterly reporting cycle ~90 days.
        US/HK=45: more frequent data updates.

    Quality gate
    ------------
    min_ic_threshold : float
        Minimum IC to pass quality gate.  Override per-market when baseline
        IC differs (e.g. HK=0.009 is below the global default of 0.01).
    min_icir_threshold : float
        Minimum ICIR to pass quality gate.

    LightGBM hyperparameters
    ------------------------
    lgb_overrides : dict
        Merged on top of _BASE_LGB_PARAMS in prediction_service.
        Includes min_child_samples: US=30, CN/HK=50 (larger for smaller universes).
    num_boost_round : int
    early_stopping_rounds : int
    """

    # Training mode
    use_temporal_sort: bool
    # Label engineering
    use_sector_neutral_labels: bool
    use_balanced_quintiles: bool
    # Feature engineering
    use_sector_rank: bool
    use_interactions: bool
    nan_threshold: float
    ffill_limit: int
    # Quality gate (per-market overrides for global PREDICTION_MIN_IC/ICIR)
    min_ic_threshold: float = 0.01
    min_icir_threshold: float = 0.10
    # LightGBM hyperparameters
    lgb_overrides: dict[str, Any] = field(default_factory=dict)
    num_boost_round: int = 1000
    early_stopping_rounds: int = 100


MARKET_CONFIGS: dict[str, MarketConfig] = {
    "us": MarketConfig(
        use_temporal_sort=False,
        use_sector_neutral_labels=True,
        use_balanced_quintiles=True,
        use_sector_rank=True,
        use_interactions=True,
        nan_threshold=0.75,
        ffill_limit=45,
        lgb_overrides={
            "learning_rate": 0.01,
            "num_leaves": 31,
            "min_child_samples": 30,
            "lambda_l2": 1.0,
        },
        num_boost_round=1000,
        early_stopping_rounds=100,
    ),
    "cn": MarketConfig(
        # CN uses temporal sort (symbol-date) — strong momentum signal in A-shares.
        # Sector groups have 3-10 stocks → sector neutralization is noise, not signal.
        # Quarterly reporting cycle → ffill up to 90 calendar days.
        use_temporal_sort=True,
        use_sector_neutral_labels=False,
        use_balanced_quintiles=False,
        use_sector_rank=False,
        use_interactions=False,
        nan_threshold=0.90,
        ffill_limit=90,
        lgb_overrides={
            "learning_rate": 0.05,
            "num_leaves": 63,
            "min_child_samples": 50,
            "lambda_l2": 0.1,
        },
        num_boost_round=500,
        early_stopping_rounds=50,
    ),
    "hk": MarketConfig(
        # HK uses temporal sort (symbol-date) — small universe (~79 stocks).
        # Sector groups have 3-10 stocks → same reasoning as CN.
        # More frequent data updates than CN → ffill 45 days.
        # Baseline IC=0.009 — below global 0.01, needs lower quality gate.
        use_temporal_sort=True,
        use_sector_neutral_labels=False,
        use_balanced_quintiles=False,
        use_sector_rank=False,
        use_interactions=False,
        nan_threshold=0.90,
        ffill_limit=45,
        min_ic_threshold=0.005,
        min_icir_threshold=0.05,
        lgb_overrides={
            "learning_rate": 0.05,
            "num_leaves": 63,
            "min_child_samples": 50,
            "lambda_l2": 0.1,
        },
        num_boost_round=500,
        early_stopping_rounds=50,
    ),
}


def get_market_config(market: str) -> MarketConfig:
    """Return MarketConfig for the given market code.

    Falls back to US config for unknown markets (safe default).
    All callers should use this function rather than accessing
    MARKET_CONFIGS directly, to benefit from the fallback.
    """
    cfg = MARKET_CONFIGS.get(market.lower())
    if cfg is None:
        logger.warning(
            "Unknown market %r — falling back to US MarketConfig. "
            "Add a dedicated entry to MARKET_CONFIGS if needed.",
            market,
        )
        cfg = MARKET_CONFIGS["us"]
    return cfg


def apply_override(market: str, overrides: dict[str, Any] | None = None) -> MarketConfig:
    """Return MarketConfig for market with optional field overrides.

    Uses dataclasses.replace() on the base config. Only fields present
    in MarketConfig are applied; unknown keys are logged and ignored.

    This is the primary entry point when callers want to tweak specific
    parameters (e.g. from a backtest config_override or LLM suggestion)
    without replacing the entire config.
    """
    cfg = get_market_config(market)
    if not overrides:
        return cfg

    valid_fields = {f.name for f in dataclasses.fields(MarketConfig)}
    filtered = {}
    unknown = []
    for k, v in overrides.items():
        if k in valid_fields:
            filtered[k] = v
        else:
            unknown.append(k)

    if unknown:
        logger.warning("Ignoring unknown MarketConfig fields: %s", unknown)

    if not filtered:
        return cfg

    return dataclasses.replace(cfg, **filtered)
