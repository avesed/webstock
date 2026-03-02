"""Unified feature pipeline for ML prediction.

Merges three feature sources into a single DataFrame:
1. Alpha158 (65 OHLCV-based features from Qlib D.features())
2. Fundamental data (~19 financial metrics, forward-filled)
3. News sentiment (~11 rolling aggregates)

The merged matrix is rank-transformed (cross-sectional percentile)
for each date, which normalizes features for LightGBM ranking.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import pandas as pd

from app.config import get_settings
from app.services.factor_service import FEATURE_NAMES
from app.services.factor_registry import factor_registry
from app.services.fundamental_service import fundamental_service
from app.services.sentiment_service import SENTIMENT_FEATURE_COLUMNS, sentiment_service
from app.services.earnings_service import earnings_service
from app.services.analyst_service import analyst_service
from app.services.options_service import options_service

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------
# Feature column definitions
# -----------------------------------------------------------------------

# 86 Alpha158 OHLCV-based features (from factor_service.py)
ALPHA158_FEATURES: list[str] = list(FEATURE_NAMES)

# ~22 fundamental financial metrics currently active in training
# (28 in _SELECT_SQL; 6 FCF features deferred until 3+ years of quarterly backfill)
FUNDAMENTAL_FEATURES: list[str] = [
    "pe_ratio", "pb_ratio", "ps_ratio", "roe", "roa",
    "profit_margin", "gross_margin", "revenue_growth_yoy", "eps",
    "debt_to_equity", "current_ratio", "dividend_yield", "market_cap",
    "forward_pe", "dividend_rate", "book_value",
    "operating_margin", "payout_ratio", "eps_growth",
    # Category 1: valuation ratios with sufficient history (2+ years coverage)
    # fcf_margin/fcf_yield/capex_ratio/buyback_yield/ev_ebitda/rd_ratio excluded:
    # only 1 year of quarterly backfill data → ~76-81% NaN, borderline sparse
    # and the missingness pattern conflates large-cap presence with signal.
    # Re-enable once quarterly backfill covers 3+ years.
    "net_cash_ratio",  # 2024-Q2 onwards → 43% non-null, worth including
    # Short interest (daily US/HK collection — initially sparse-filtered)
    "short_pct_float", "short_ratio",
]

# ~9 sentiment rolling features
SENTIMENT_FEATURES: list[str] = list(SENTIMENT_FEATURE_COLUMNS)

# Category 2: EPS surprise (forward-filled from quarterly events)
# last_eps_surprise excluded from training: only ~30% of US universe has
# earnings history in stock_earnings_events → 70%+ NaN → LightGBM treats
# NaN as a split signal ("has earnings data?" = large-cap proxy), causing
# spurious overfitting in 2-4 iterations. Re-enable once earnings coverage
# reaches ~80% of the universe (currently ~116/503 symbols = 23% non-null).
EARNINGS_FEATURES: list[str] = []

# Category 3: Analyst snapshots (daily collection, cold-start ~3 months)
ANALYST_FEATURES: list[str] = [
    "analyst_buy_ratio", "analyst_net_score",
    "eps_revision_score", "target_premium", "growth_est_next_y",
]

# Category 3 (insider): daily collection, cold-start ~3 months
INSIDER_FEATURES: list[str] = ["net_shares_pct", "insider_ownership_pct"]

# Category 4: Options put/call ratio (US only, cold-start ~3 months)
OPTIONS_FEATURES: list[str] = ["put_call_ratio"]

# ThreadPoolExecutor for synchronous Qlib calls (D.features)
# Single thread since Qlib's global state is not thread-safe for concurrent inits
_qlib_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="feature-qlib")


class FeatureService:
    """Build unified feature matrices for ML training and inference.

    Merges Alpha158 technical features (Qlib), fundamental financial
    metrics, and news sentiment aggregates. Applies cross-sectional
    rank normalization to produce LightGBM-ready input.
    """

    async def build_feature_matrix(
        self,
        market: str,
        symbols: list[str],
        start_date: str,
        end_date: str,
        include_fundamental: bool = True,
        include_sentiment: bool = True,
    ) -> pd.DataFrame:
        """Build a merged, rank-normalized feature matrix.

        Args:
            market: Market code (us, hk, cn, etc.).
            symbols: List of stock symbols.
            start_date: Start date (YYYY-MM-DD).
            end_date: End date (YYYY-MM-DD).
            include_fundamental: Whether to include fundamental features.
            include_sentiment: Whether to include sentiment features.

        Returns:
            DataFrame with columns: symbol, date, + feature columns.
            All feature values are rank-transformed to [0, 1] percentiles
            cross-sectionally (per date). Empty DataFrame on total failure.
        """
        if not symbols:
            logger.warning("build_feature_matrix called with empty symbol list")
            return pd.DataFrame()

        logger.info(
            "Building feature matrix: market=%s, symbols=%d, %s~%s, "
            "fundamental=%s, sentiment=%s",
            market, len(symbols), start_date, end_date,
            include_fundamental, include_sentiment,
        )

        # Step 1: Alpha158 技术特征 (同步, 需要在线程池中运行)
        loop = asyncio.get_running_loop()
        try:
            alpha_df = await loop.run_in_executor(
                _qlib_executor,
                self._get_alpha158_sync,
                market, symbols, start_date, end_date,
            )
        except Exception as e:
            logger.error("Alpha158 feature extraction failed: %s", e)
            alpha_df = pd.DataFrame()

        if alpha_df.empty:
            logger.warning(
                "Alpha158 returned empty DataFrame; cannot build feature matrix"
            )
            return pd.DataFrame()

        logger.info(
            "Alpha158 features: %d rows x %d columns",
            len(alpha_df), len(alpha_df.columns) - 2,  # minus symbol, date
        )

        # Step 2: RD-Agent 发现的因子 (Qlib 表达式, 同步线程池)
        rdagent_df = pd.DataFrame()
        try:
            active_factors = await factor_registry.get_active_factors(market)
            if active_factors:
                rdagent_df = await loop.run_in_executor(
                    _qlib_executor,
                    self._get_rdagent_factors_sync,
                    market, symbols, start_date, end_date, active_factors,
                )
                if not rdagent_df.empty:
                    logger.info(
                        "RD-Agent factors: %d rows x %d columns",
                        len(rdagent_df), len(rdagent_df.columns) - 2,
                    )
        except Exception as e:
            logger.warning("RD-Agent factor retrieval failed: %s", e)

        # Step 3 & 4: 并行获取基本面、情绪以及新信号特征
        tasks: list[asyncio.Task] = []
        task_names: list[str] = []

        fundamental_df = pd.DataFrame()
        sentiment_df = pd.DataFrame()
        earnings_df = pd.DataFrame()
        analyst_df = pd.DataFrame()
        insider_df = pd.DataFrame()
        options_df = pd.DataFrame()

        if include_fundamental:
            tasks.append(
                asyncio.create_task(
                    self._safe_get_fundamentals(symbols, start_date, end_date),
                )
            )
            task_names.append("fundamentals")

        if include_sentiment:
            tasks.append(
                asyncio.create_task(
                    self._safe_get_sentiment(symbols, start_date, end_date),
                )
            )
            task_names.append("sentiment")

        # Always attempt all 4 new signal categories
        tasks.append(asyncio.create_task(
            self._safe_get_earnings(symbols, start_date, end_date),
        ))
        task_names.append("earnings")

        tasks.append(asyncio.create_task(
            self._safe_get_analyst(symbols, start_date, end_date),
        ))
        task_names.append("analyst")

        tasks.append(asyncio.create_task(
            self._safe_get_insider(symbols, start_date, end_date),
        ))
        task_names.append("insider")

        tasks.append(asyncio.create_task(
            self._safe_get_options(symbols, market, start_date, end_date),
        ))
        task_names.append("options")

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for name, res in zip(task_names, results):
                if isinstance(res, pd.DataFrame):
                    if name == "fundamentals":
                        fundamental_df = res
                    elif name == "sentiment":
                        sentiment_df = res
                    elif name == "earnings":
                        earnings_df = res
                    elif name == "analyst":
                        analyst_df = res
                    elif name == "insider":
                        insider_df = res
                    elif name == "options":
                        options_df = res
                elif isinstance(res, Exception):
                    logger.warning("%s feature fetch failed: %s", name, res)

        # Step 4: 合并 — 以 Alpha158 为基准左连接
        merged = alpha_df.copy()
        merged["date"] = pd.to_datetime(merged["date"])

        if not fundamental_df.empty:
            fundamental_df["date"] = pd.to_datetime(fundamental_df["date"])
            # 只保留需要的列, 避免冲突
            fund_cols = ["symbol", "date"] + [
                c for c in FUNDAMENTAL_FEATURES if c in fundamental_df.columns
            ]
            merged = merged.merge(
                fundamental_df[fund_cols],
                on=["symbol", "date"],
                how="left",
            )
            logger.info(
                "Merged fundamental features: %d columns added",
                len(fund_cols) - 2,
            )

        if not sentiment_df.empty:
            sentiment_df["date"] = pd.to_datetime(sentiment_df["date"])
            sent_cols = ["symbol", "date"] + [
                c for c in SENTIMENT_FEATURES if c in sentiment_df.columns
            ]
            merged = merged.merge(
                sentiment_df[sent_cols],
                on=["symbol", "date"],
                how="left",
            )
            logger.info(
                "Merged sentiment features: %d columns added",
                len(sent_cols) - 2,
            )

        if not earnings_df.empty:
            earnings_df["date"] = pd.to_datetime(earnings_df["date"])
            earn_cols = ["symbol", "date"] + [
                c for c in EARNINGS_FEATURES if c in earnings_df.columns
            ]
            merged = merged.merge(earnings_df[earn_cols], on=["symbol", "date"], how="left")
            logger.info("Merged earnings features: %d columns added", len(earn_cols) - 2)

        if not analyst_df.empty:
            analyst_df["date"] = pd.to_datetime(analyst_df["date"])
            ana_cols = ["symbol", "date"] + [
                c for c in ANALYST_FEATURES if c in analyst_df.columns
            ]
            merged = merged.merge(analyst_df[ana_cols], on=["symbol", "date"], how="left")
            logger.info("Merged analyst features: %d columns added", len(ana_cols) - 2)

        if not insider_df.empty:
            insider_df["date"] = pd.to_datetime(insider_df["date"])
            ins_cols = ["symbol", "date"] + [
                c for c in INSIDER_FEATURES if c in insider_df.columns
            ]
            merged = merged.merge(insider_df[ins_cols], on=["symbol", "date"], how="left")
            logger.info("Merged insider features: %d columns added", len(ins_cols) - 2)

        if not options_df.empty:
            options_df["date"] = pd.to_datetime(options_df["date"])
            opt_cols = ["symbol", "date"] + [
                c for c in OPTIONS_FEATURES if c in options_df.columns
            ]
            merged = merged.merge(options_df[opt_cols], on=["symbol", "date"], how="left")
            logger.info("Merged options features: %d columns added", len(opt_cols) - 2)

        if not rdagent_df.empty:
            rdagent_df["date"] = pd.to_datetime(rdagent_df["date"])
            rd_cols = ["symbol", "date"] + [
                c for c in rdagent_df.columns if c not in ("symbol", "date")
            ]
            merged = merged.merge(
                rdagent_df[rd_cols],
                on=["symbol", "date"],
                how="left",
            )
            logger.info(
                "Merged RD-Agent discovered factors: %d columns added",
                len(rd_cols) - 2,
            )

        # Ensure all feature columns are float64 (PostgreSQL returns Decimal
        # types for numeric columns, which break float arithmetic)
        feature_cols = [c for c in merged.columns if c not in ("symbol", "date")]
        for col in feature_cols:
            merged[col] = pd.to_numeric(merged[col], errors="coerce").astype("float64")

        # Step 5.5: 特征质量过滤 — 剔除 NaN 率过高的特征列
        # 高 NaN 列（如无基本面/情绪数据的市场）会稀释有效特征被采样到的概率
        # Threshold 0.75: drops features that are >75% NaN in the training matrix.
        # This covers two categories:
        #   1. Truly absent features (analyst/insider/options, 100% NaN until 3mo of
        #      daily collection — analyst/insider/options cold-start period)
        #   2. High-NaN fundamental/sentiment features (revenue_growth_yoy ~76% NaN,
        #      eps_growth ~78% NaN, sentiment rolling aggregates ~80% NaN in 2yr window)
        #      These have enough NaN to create spurious "has-data?" splits in LightGBM
        #      that overfit to dataset membership rather than signal.
        # Note: last_eps_surprise (77% NaN, earnings DB coverage ~30% of universe) is
        # excluded explicitly via EARNINGS_FEATURES=[] for the same spurious-split reason.
        merged, dropped = self._drop_sparse_features(merged, max_nan_ratio=0.75)

        # Step 6: 截面排名变换 — 每个日期内将所有股票的特征排名到百分位 [0, 1]
        # Rank transform creates uniform distributions optimal for tree-based
        # models: balanced splits at every threshold for maximum information gain.
        merged = self._rank_transform(merged)

        remaining = len([c for c in merged.columns if c not in ("symbol", "date")])
        logger.info(
            "Feature matrix built: %d rows x %d feature columns (dropped %d sparse)",
            len(merged), remaining, dropped,
        )
        return merged

    # ------------------------------------------------------------------
    # Alpha158 (synchronous, runs in ThreadPoolExecutor)
    # ------------------------------------------------------------------

    def _get_alpha158_sync(
        self,
        market: str,
        symbols: list[str],
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """Compute Alpha158 features via Qlib D.features().

        Synchronous method designed for executor use. Initializes Qlib
        for the target market and queries D.features() with the 65
        single-stock expression list.

        Returns:
            Flat DataFrame with columns: symbol, date, + 65 feature names.
        """
        from app.context import QlibContext
        from app.services.factor_service import SINGLE_STOCK_FEATURES
        from app.utils.symbol_mapping import (
            normalize_symbol_for_qlib,
            qlib_to_webstock,
        )

        settings = get_settings()

        # Qlib 初始化 (进程级别, 线程安全锁)
        try:
            QlibContext.ensure_init(market, settings.QLIB_DATA_DIR)
        except Exception as e:
            logger.error("Qlib init failed for market=%s: %s", market, e)
            return pd.DataFrame()

        from qlib.data import D

        # 转换符号到 Qlib 格式
        qlib_symbols = [normalize_symbol_for_qlib(s, market) for s in symbols]

        # 建立 Qlib 符号到 WebStock 符号的映射
        qlib_to_ws = {}
        for ws_sym, q_sym in zip(symbols, qlib_symbols):
            qlib_to_ws[q_sym] = ws_sym

        logger.info(
            "D.features() call: %d symbols, %s~%s, %d features",
            len(qlib_symbols), start_date, end_date, len(SINGLE_STOCK_FEATURES),
        )

        try:
            df = D.features(
                instruments=qlib_symbols,
                fields=SINGLE_STOCK_FEATURES,
                start_time=start_date,
                end_time=end_date,
            )
        except Exception as e:
            logger.error("D.features() failed: %s", e)
            return pd.DataFrame()

        if df.empty:
            logger.warning("D.features() returned empty DataFrame")
            return pd.DataFrame()

        # 展开 MultiIndex (instrument, datetime) -> 平坦列
        df.columns = FEATURE_NAMES[: len(df.columns)]

        if hasattr(df.index, "levels") and len(df.index.levels) == 2:
            # MultiIndex: (instrument, datetime)
            df = df.reset_index()
            df.columns = ["qlib_symbol", "date"] + list(df.columns[2:])
            # 将 Qlib 符号映射回 WebStock 符号
            df["symbol"] = df["qlib_symbol"].map(
                lambda s: qlib_to_ws.get(s, qlib_to_webstock(s, market))
            )
            df = df.drop(columns=["qlib_symbol"])
        else:
            # 单股票: 只有 datetime index
            df = df.reset_index()
            df.columns = ["date"] + list(df.columns[1:])
            df["symbol"] = symbols[0] if len(symbols) == 1 else "UNKNOWN"

        # 确保 date 列格式统一
        df["date"] = pd.to_datetime(df["date"])

        # 重排列顺序: symbol, date, features...
        feature_cols = [c for c in df.columns if c not in ("symbol", "date")]
        df = df[["symbol", "date"] + feature_cols]

        logger.info(
            "Alpha158 extraction complete: %d rows, %d symbols",
            len(df), df["symbol"].nunique(),
        )
        return df

    # ------------------------------------------------------------------
    # RD-Agent discovered factors (synchronous, runs in ThreadPoolExecutor)
    # ------------------------------------------------------------------

    def _get_rdagent_factors_sync(
        self,
        market: str,
        symbols: list[str],
        start_date: str,
        end_date: str,
        active_factors: list[dict],
    ) -> pd.DataFrame:
        """Compute RD-Agent discovered factor features via Qlib D.features().

        Each active factor has an ``expression`` field containing a Qlib
        expression string (e.g. "Mean($close,5)/$close"). These are evaluated
        alongside the Alpha158 features using the same Qlib data backend.

        Returns:
            Flat DataFrame with columns: symbol, date, + factor_name columns.
            Empty DataFrame if no expressions are valid.
        """
        from app.context import QlibContext
        from app.utils.symbol_mapping import (
            normalize_symbol_for_qlib,
            qlib_to_webstock,
        )

        settings = get_settings()
        try:
            QlibContext.ensure_init(market, settings.QLIB_DATA_DIR)
        except Exception as e:
            logger.error("Qlib init failed for RD-Agent factors: %s", e)
            return pd.DataFrame()

        from qlib.data import D

        expressions = [f["expression"] for f in active_factors]
        factor_names = [f["name"] for f in active_factors]

        qlib_symbols = [normalize_symbol_for_qlib(s, market) for s in symbols]
        qlib_to_ws = {q: w for w, q in zip(symbols, qlib_symbols)}

        logger.info(
            "D.features() for %d RD-Agent factors, %d symbols",
            len(expressions), len(qlib_symbols),
        )

        try:
            df = D.features(
                instruments=qlib_symbols,
                fields=expressions,
                start_time=start_date,
                end_time=end_date,
            )
        except Exception as e:
            logger.error("D.features() failed for RD-Agent factors: %s", e)
            return pd.DataFrame()

        if df.empty:
            return pd.DataFrame()

        # Rename columns to factor names
        df.columns = factor_names[: len(df.columns)]

        if hasattr(df.index, "levels") and len(df.index.levels) == 2:
            df = df.reset_index()
            df.columns = ["qlib_symbol", "date"] + list(df.columns[2:])
            df["symbol"] = df["qlib_symbol"].map(
                lambda s: qlib_to_ws.get(s, qlib_to_webstock(s, market))
            )
            df = df.drop(columns=["qlib_symbol"])
        else:
            df = df.reset_index()
            df.columns = ["date"] + list(df.columns[1:])
            df["symbol"] = symbols[0] if len(symbols) == 1 else "UNKNOWN"

        df["date"] = pd.to_datetime(df["date"])
        feat_cols = [c for c in df.columns if c not in ("symbol", "date")]
        return df[["symbol", "date"] + feat_cols]

    # ------------------------------------------------------------------
    # Safe wrappers (per-source error isolation)
    # ------------------------------------------------------------------

    @staticmethod
    async def _safe_get_fundamentals(
        symbols: list[str], start_date: str, end_date: str
    ) -> pd.DataFrame:
        """Fetch fundamentals with error isolation."""
        try:
            return await fundamental_service.get_fundamentals(
                symbols, start_date, end_date
            )
        except Exception as e:
            logger.warning("Fundamental feature retrieval failed: %s", e)
            return pd.DataFrame()

    @staticmethod
    async def _safe_get_sentiment(
        symbols: list[str], start_date: str, end_date: str
    ) -> pd.DataFrame:
        """Fetch sentiment features with error isolation."""
        try:
            return await sentiment_service.get_sentiment_features(
                symbols, start_date, end_date
            )
        except Exception as e:
            logger.warning("Sentiment feature retrieval failed: %s", e)
            return pd.DataFrame()

    @staticmethod
    async def _safe_get_earnings(
        symbols: list[str], start_date: str, end_date: str
    ) -> pd.DataFrame:
        """Fetch EPS surprise features with error isolation."""
        try:
            return await earnings_service.get_earnings_features(
                symbols, start_date, end_date
            )
        except Exception as e:
            logger.warning("Earnings feature retrieval failed: %s", e)
            return pd.DataFrame()

    @staticmethod
    async def _safe_get_analyst(
        symbols: list[str], start_date: str, end_date: str
    ) -> pd.DataFrame:
        """Fetch analyst snapshot features with error isolation."""
        try:
            return await analyst_service.get_analyst_features(
                symbols, start_date, end_date
            )
        except Exception as e:
            logger.warning("Analyst feature retrieval failed: %s", e)
            return pd.DataFrame()

    @staticmethod
    async def _safe_get_insider(
        symbols: list[str], start_date: str, end_date: str
    ) -> pd.DataFrame:
        """Fetch insider activity features with error isolation."""
        try:
            return await analyst_service.get_insider_features(
                symbols, start_date, end_date
            )
        except Exception as e:
            logger.warning("Insider feature retrieval failed: %s", e)
            return pd.DataFrame()

    @staticmethod
    async def _safe_get_options(
        symbols: list[str], market: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """Fetch options put/call ratio features with error isolation."""
        try:
            return await options_service.get_options_features(
                symbols, start_date, end_date
            )
        except Exception as e:
            logger.warning("Options feature retrieval failed: %s", e)
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # Feature quality control
    # ------------------------------------------------------------------

    @staticmethod
    def _drop_sparse_features(
        df: pd.DataFrame, max_nan_ratio: float = 0.90,
    ) -> tuple[pd.DataFrame, int]:
        """Drop feature columns with excessively high NaN rates.

        When a market lacks fundamental or sentiment data, those 19+11
        features are all NaN. Keeping them wastes feature_fraction budget
        during LightGBM training (randomly sampled NaN columns dilute the
        effective feature set).

        Args:
            df: Feature DataFrame with symbol, date, + feature columns.
            max_nan_ratio: Drop columns exceeding this NaN rate (default 90%).

        Returns:
            (filtered_df, count_of_dropped_columns)
        """
        feature_cols = [c for c in df.columns if c not in ("symbol", "date")]
        if not feature_cols:
            return df, 0

        nan_ratios = df[feature_cols].isna().mean()
        sparse_cols = nan_ratios[nan_ratios > max_nan_ratio].index.tolist()

        if sparse_cols:
            logger.warning(
                "Dropping %d sparse features (>%.0f%% NaN): %s",
                len(sparse_cols),
                max_nan_ratio * 100,
                sparse_cols[:10],  # log first 10
            )
            df = df.drop(columns=sparse_cols)

        return df, len(sparse_cols)

    # ------------------------------------------------------------------
    # Rank transform
    # ------------------------------------------------------------------

    @staticmethod
    def _cross_sectional_normalize(df: pd.DataFrame) -> pd.DataFrame:
        """Apply cross-sectional MAD-based robust normalization per date.

        For each date, feature values are:
        1. Median-centered
        2. Scaled by MAD (median absolute deviation) × 1.4826
        3. Clipped to [-3, 3] (removes extreme outliers)

        This preserves magnitude information (unlike rank transform) while
        making features comparable across dates. Tree-based models like
        LightGBM don't strictly need normalization, but clipping outliers
        prevents a few extreme values from dominating split decisions.

        NaN values remain NaN (LightGBM handles them natively).
        """
        import numpy as np

        feature_cols = [c for c in df.columns if c not in ("symbol", "date")]
        if not feature_cols:
            return df

        result = df.copy()

        for col in feature_cols:
            def _robust_norm(x: "pd.Series") -> "pd.Series":
                median = x.median()
                mad = (x - median).abs().median()
                # MAD → σ (for normal distribution, σ ≈ 1.4826 × MAD)
                scale = mad * 1.4826
                if scale < 1e-10:
                    # All values identical or single non-NaN → center only
                    return x - median
                normed = (x - median) / scale
                return normed.clip(-3.0, 3.0)

            result[col] = result.groupby("date")[col].transform(_robust_norm)

        return result

    @staticmethod
    def _rank_transform(df: pd.DataFrame) -> pd.DataFrame:
        """Apply cross-sectional percentile ranking per date.

        DEPRECATED: Kept for backward compatibility. Rank transform destroys
        magnitude information which hurts tree-based models. Use
        _cross_sectional_normalize() instead.
        """
        feature_cols = [c for c in df.columns if c not in ("symbol", "date")]
        if not feature_cols:
            return df

        result = df.copy()
        for col in feature_cols:
            result[col] = result.groupby("date")[col].rank(pct=True)

        return result

    # ------------------------------------------------------------------
    # Feature name helpers
    # ------------------------------------------------------------------

    def get_feature_names(
        self,
        include_fundamental: bool = True,
        include_sentiment: bool = True,
    ) -> list[str]:
        """Return the full list of feature column names.

        Useful for model training configuration and feature importance
        analysis.

        Args:
            include_fundamental: Include fundamental feature names.
            include_sentiment: Include sentiment feature names.

        Returns:
            Ordered list of feature column name strings.
        """
        names = list(ALPHA158_FEATURES)
        if include_fundamental:
            names.extend(FUNDAMENTAL_FEATURES)
        if include_sentiment:
            names.extend(SENTIMENT_FEATURES)
        return names

    def get_feature_count(
        self,
        include_fundamental: bool = True,
        include_sentiment: bool = True,
    ) -> int:
        """Return total number of features for the given configuration."""
        return len(self.get_feature_names(include_fundamental, include_sentiment))


# Module singleton
feature_service = FeatureService()
