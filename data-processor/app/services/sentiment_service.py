"""News sentiment feature aggregation.

Fetches per-symbol daily sentiment aggregates from NewsForge's internal
API and computes rolling features for the ML prediction pipeline.

Results cached in Redis for 24 hours.
"""

import hashlib
import logging
from typing import Optional

import httpx
import msgpack
import pandas as pd
import redis.asyncio as aioredis

from app.config import get_settings

logger = logging.getLogger(__name__)

# Redis cache TTL: 24 hours
_CACHE_TTL = 86400

# Columns returned by this service
SENTIMENT_FEATURE_COLUMNS = [
    "sentiment_avg",
    "article_count",
    "bullish_ratio",
    "content_score_avg",
    "sentiment_7d_ma",
    "sentiment_30d_ma",
    "article_count_7d",
    "bullish_ratio_7d",
    "sentiment_volatility_7d",
    "has_news_7d",
    "has_news_30d",
]


def _build_cache_key(symbols: list[str], start_date: str, end_date: str) -> str:
    """Build deterministic Redis cache key from query parameters."""
    content = "|".join(sorted(symbols)) + f"|{start_date}|{end_date}"
    digest = hashlib.md5(content.encode()).hexdigest()
    return f"pred:sentiment:{digest}"


_redis_client: Optional[aioredis.Redis] = None


def _get_redis_client() -> aioredis.Redis:
    """Return a module-level shared async Redis client (lazy singleton)."""
    global _redis_client
    if _redis_client is None:
        settings = get_settings()
        _redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=False)
    return _redis_client


class SentimentService:
    """Aggregate news sentiment into per-symbol daily features.

    Features:
    - sentiment_avg: Raw daily average sentiment score
    - article_count: Number of articles per day
    - bullish_ratio: Proportion bullish vs bearish (-1 to 1)
    - content_score_avg: Average content quality score
    - sentiment_7d_ma: 7-day moving average of sentiment
    - sentiment_30d_ma: 30-day moving average of sentiment
    - article_count_7d: 7-day rolling article count sum
    - bullish_ratio_7d: 7-day rolling bullish ratio average
    - sentiment_volatility_7d: 7-day rolling std of sentiment
    """

    async def get_sentiment_features(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """Compute sentiment features for the given symbols and date range.

        Checks Redis cache first. On cache miss, queries news table via
        asyncpg, computes rolling features in pandas, and caches the result.

        Args:
            symbols: List of stock symbols.
            start_date: Start date (YYYY-MM-DD).
            end_date: End date (YYYY-MM-DD).

        Returns:
            DataFrame with columns: symbol, date, + SENTIMENT_FEATURE_COLUMNS.
            Empty DataFrame if no news data available.
        """
        if not symbols:
            return pd.DataFrame()

        # 1. 检查 Redis 缓存
        cached = await self._read_cache(symbols, start_date, end_date)
        if cached is not None:
            logger.debug(
                "Sentiment cache hit: %d symbols, %s~%s",
                len(symbols), start_date, end_date,
            )
            return cached

        # 2. 查询新闻表获取原始日汇总
        raw_df = await self._query_raw_aggregates(symbols, start_date, end_date)

        # 3. 计算滚动特征
        result = self._compute_rolling_features(raw_df, symbols, start_date, end_date)

        # 4. 写入缓存
        await self._write_cache(result, symbols, start_date, end_date)

        logger.info(
            "Sentiment features computed: %d symbols, %d rows, %s~%s",
            len(symbols), len(result), start_date, end_date,
        )
        return result

    # ------------------------------------------------------------------
    # Raw data query
    # ------------------------------------------------------------------

    async def _query_raw_aggregates(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """Fetch daily sentiment aggregates from NewsForge API."""
        settings = get_settings()
        newsforge_url = (getattr(settings, "NEWSFORGE_URL", "") or "").rstrip("/")
        newsforge_key = getattr(settings, "NEWSFORGE_API_KEY", "") or ""

        if not newsforge_url or not newsforge_key:
            logger.warning("NewsForge URL/API key not configured for sentiment query")
            return pd.DataFrame()

        # Request 60 extra days for rolling window computation
        from datetime import date as date_type, timedelta
        try:
            start = date_type.fromisoformat(start_date)
            end = date_type.fromisoformat(end_date)
        except ValueError as e:
            logger.error("Invalid date format: start=%r, end=%r: %s", start_date, end_date, e)
            return pd.DataFrame()

        lookback_start = (start - timedelta(days=60)).isoformat()

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    f"{newsforge_url}/api/internal/sentiment/batch",
                    headers={"X-API-Key": newsforge_key},
                    params={
                        "symbols": ",".join(symbols),
                        "start_date": lookback_start,
                        "end_date": end_date,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.error("NewsForge sentiment batch request failed: %s", e)
            return pd.DataFrame()

        rows = []
        for symbol_key, symbol_data in data.items():
            timeline = symbol_data.get("timeline") or []
            for entry in timeline:
                total = entry.get("total", 0)
                if total == 0:
                    continue
                bullish = entry.get("bullish", 0)
                bearish = entry.get("bearish", 0)
                rows.append({
                    "symbol": symbol_key,
                    "date": entry.get("date"),
                    "sentiment_avg": entry.get("avg_score", 0.0),
                    "article_count": float(total),
                    "bullish_ratio": (bullish - bearish) / total if total > 0 else 0.0,
                    "content_score_avg": entry.get("avg_value_score", 0.0),
                })

        if not rows:
            logger.debug("No sentiment data from NewsForge for %d symbols", len(symbols))
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])

        for col in ("sentiment_avg", "article_count", "bullish_ratio", "content_score_avg"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        return df

    # ------------------------------------------------------------------
    # Rolling feature computation
    # ------------------------------------------------------------------

    def _compute_rolling_features(
        self,
        raw_df: pd.DataFrame,
        symbols: list[str],
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """Compute per-symbol rolling sentiment features.

        For symbols with no news at all, creates neutral-filled rows.
        For days without news within a symbol, fills with neutral values
        (sentiment=0, count=0) before computing rolling windows.
        """
        if raw_df.empty:
            # 没有新闻数据: 返回 NaN-filled DataFrame (LightGBM handles NaN natively)
            return self._build_neutral_frame(symbols, start_date, end_date)

        full_dates = pd.date_range(start=start_date, end=end_date, freq="D")
        parts: list[pd.DataFrame] = []

        for symbol in symbols:
            if symbol in raw_df["symbol"].values:
                mask = raw_df["symbol"] == symbol
                symbol_df = raw_df.loc[mask].copy()
                symbol_df = symbol_df.set_index("date").sort_index()
            else:
                # 该股票完全没有新闻 — 创建带列名的空 DataFrame
                # 避免 reindex 后缺少列导致 KeyError
                symbol_df = pd.DataFrame(
                    index=pd.DatetimeIndex([], name="date"),
                    columns=["sentiment_avg", "article_count", "bullish_ratio", "content_score_avg"],
                )

            # 对齐到完整日期范围
            symbol_df = symbol_df.reindex(full_dates)
            symbol_df["symbol"] = symbol

            # article_count: 无新闻日 = 0 (确实没有文章)
            symbol_df["article_count"] = symbol_df["article_count"].fillna(0.0)

            # sentiment_avg, bullish_ratio, content_score_avg: 保留 NaN
            # LightGBM 原生支持 NaN，能自动学习最佳分裂方向
            # 不再 fillna(0.0)，避免 "无新闻" 与 "中性新闻" 混淆

            # 滚动窗口特征 (NaN 会被 rolling 自动跳过)
            symbol_df["sentiment_7d_ma"] = (
                symbol_df["sentiment_avg"].rolling(7, min_periods=1).mean()
            )
            symbol_df["sentiment_30d_ma"] = (
                symbol_df["sentiment_avg"].rolling(30, min_periods=1).mean()
            )
            symbol_df["article_count_7d"] = (
                symbol_df["article_count"].rolling(7, min_periods=1).sum()
            )
            symbol_df["bullish_ratio_7d"] = (
                symbol_df["bullish_ratio"].rolling(7, min_periods=1).mean()
            )
            symbol_df["sentiment_volatility_7d"] = (
                symbol_df["sentiment_avg"].rolling(7, min_periods=2).std()
            )

            # has_news: 显式布尔特征，让模型学习 "有新闻" vs "无新闻"
            symbol_df["has_news_7d"] = (
                symbol_df["article_count"].rolling(7, min_periods=1).sum().gt(0).astype(float)
            )
            symbol_df["has_news_30d"] = (
                symbol_df["article_count"].rolling(30, min_periods=1).sum().gt(0).astype(float)
            )

            symbol_df = symbol_df.reset_index().rename(columns={"index": "date"})
            parts.append(symbol_df)

        if not parts:
            return pd.DataFrame()

        result = pd.concat(parts, ignore_index=True)

        # 保留目标列
        keep_cols = ["symbol", "date"] + SENTIMENT_FEATURE_COLUMNS
        result = result[[c for c in keep_cols if c in result.columns]]
        return result

    @staticmethod
    def _build_neutral_frame(
        symbols: list[str], start_date: str, end_date: str
    ) -> pd.DataFrame:
        """Build a DataFrame when no news data exists.

        article_count and has_news fields are 0 (factual: no articles).
        Sentiment fields are NaN (unknown: no data to derive sentiment from).
        """
        import numpy as np

        dates = pd.date_range(start=start_date, end=end_date, freq="D")
        # Fields that are NaN when no news exists (sentiment is unknown)
        nan_cols = {
            "sentiment_avg", "bullish_ratio", "content_score_avg",
            "sentiment_7d_ma", "sentiment_30d_ma",
            "bullish_ratio_7d", "sentiment_volatility_7d",
        }
        rows = []
        for symbol in symbols:
            for d in dates:
                row: dict = {"symbol": symbol, "date": d}
                for col in SENTIMENT_FEATURE_COLUMNS:
                    row[col] = np.nan if col in nan_cols else 0.0
                rows.append(row)
        return pd.DataFrame(rows) if rows else pd.DataFrame()

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Redis cache (async client)
    # ------------------------------------------------------------------

    async def _read_cache(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
    ) -> Optional[pd.DataFrame]:
        """Try to read cached sentiment features from Redis."""
        key = _build_cache_key(symbols, start_date, end_date)
        try:
            r = _get_redis_client()
            data = await r.get(key)
            if data is None:
                return None
            unpacked = msgpack.unpackb(data, raw=False)
            df = pd.DataFrame(unpacked)
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
            return df
        except Exception as e:
            logger.warning("Redis cache read failed (non-fatal): %s", e)
            return None

    async def _write_cache(
        self,
        df: pd.DataFrame,
        symbols: list[str],
        start_date: str,
        end_date: str,
    ) -> None:
        """Write sentiment features to Redis cache."""
        if df.empty:
            return
        key = _build_cache_key(symbols, start_date, end_date)
        try:
            # 序列化: 将 date 转为 ISO string 以便 msgpack 处理
            cache_df = df.copy()
            if "date" in cache_df.columns:
                cache_df["date"] = cache_df["date"].astype(str)
            packed = msgpack.packb(cache_df.to_dict(orient="list"), use_bin_type=True)

            r = _get_redis_client()
            await r.setex(key, _CACHE_TTL, packed)
            logger.debug("Cached sentiment features: key=%s, rows=%d", key, len(df))
        except Exception as e:
            logger.warning("Redis cache write failed (non-fatal): %s", e)


# Module singleton
sentiment_service = SentimentService()
