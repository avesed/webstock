"""News sentiment feature aggregation.

Queries the news table (via asyncpg) to compute per-symbol daily
sentiment features for the ML prediction pipeline. Features include
rolling averages, article counts, and sentiment volatility.

Results cached in Redis for 24 hours.
"""

import hashlib
import logging
from typing import Optional

import msgpack
import pandas as pd
import redis

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
]

# Query raw daily aggregates from news table.
# We fetch 60 extra days before start_date to have enough data
# for the 30-day rolling window.
_RAW_AGGREGATE_SQL = """
    SELECT
        symbol,
        DATE(published_at) as date,
        AVG(sentiment_score) as sentiment_avg,
        COUNT(*) as article_count,
        AVG(CASE WHEN sentiment_tag = 'bullish' THEN 1.0
                 WHEN sentiment_tag = 'bearish' THEN -1.0
                 ELSE 0.0 END) as bullish_ratio,
        AVG(content_score) as content_score_avg
    FROM news
    WHERE filter_status IN ('keep', 'useful')
      AND symbol IS NOT NULL
      AND symbol = ANY($1::text[])
      AND published_at >= $2::date - INTERVAL '60 days'
      AND published_at < $3::date + INTERVAL '1 day'
    GROUP BY symbol, DATE(published_at)
    ORDER BY symbol, date
"""


def _build_cache_key(symbols: list[str], start_date: str, end_date: str) -> str:
    """Build deterministic Redis cache key from query parameters."""
    content = "|".join(sorted(symbols)) + f"|{start_date}|{end_date}"
    digest = hashlib.md5(content.encode()).hexdigest()
    return f"pred:sentiment:{digest}"


_redis_client: Optional[redis.Redis] = None


def _get_redis_client() -> redis.Redis:
    """Return a module-level shared Redis client (lazy singleton)."""
    global _redis_client
    if _redis_client is None:
        settings = get_settings()
        _redis_client = redis.from_url(settings.REDIS_URL, decode_responses=False)
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
        cached = self._read_cache(symbols, start_date, end_date)
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
        self._write_cache(result, symbols, start_date, end_date)

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
        """Query news table for daily sentiment aggregates via asyncpg."""
        from app.core.settings_cache import settings_cache

        pool = settings_cache.pool
        if not pool:
            logger.warning("DB pool not available for sentiment query")
            return pd.DataFrame()

        try:
            async with pool.acquire(timeout=10) as conn:
                rows = await conn.fetch(
                    _RAW_AGGREGATE_SQL, symbols, start_date, end_date
                )
        except Exception as e:
            logger.error("Failed to query news sentiment aggregates: %s", e)
            return pd.DataFrame()

        if not rows:
            logger.debug("No news data found for %d symbols", len(symbols))
            return pd.DataFrame()

        df = pd.DataFrame([dict(r) for r in rows])
        df["date"] = pd.to_datetime(df["date"])

        # 将数值列转换为 float (asyncpg 可能返回 Decimal)
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
            # 没有新闻数据: 返回全零 neutral DataFrame
            return self._build_neutral_frame(symbols, start_date, end_date)

        full_dates = pd.date_range(start=start_date, end=end_date, freq="D")
        parts: list[pd.DataFrame] = []

        for symbol in symbols:
            if symbol in raw_df["symbol"].values:
                mask = raw_df["symbol"] == symbol
                symbol_df = raw_df.loc[mask].copy()
                symbol_df = symbol_df.set_index("date").sort_index()
            else:
                # 该股票完全没有新闻
                symbol_df = pd.DataFrame(index=pd.DatetimeIndex([], name="date"))

            # 对齐到完整日期范围, 无新闻日填充中性值
            symbol_df = symbol_df.reindex(full_dates)
            symbol_df["symbol"] = symbol
            symbol_df["sentiment_avg"] = symbol_df["sentiment_avg"].fillna(0.0)
            symbol_df["article_count"] = symbol_df["article_count"].fillna(0.0)
            symbol_df["bullish_ratio"] = symbol_df["bullish_ratio"].fillna(0.0)
            symbol_df["content_score_avg"] = symbol_df["content_score_avg"].fillna(0.0)

            # 滚动窗口特征
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
                symbol_df["sentiment_avg"].rolling(7, min_periods=2).std().fillna(0.0)
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
        """Build an all-neutral DataFrame when no news data exists."""
        dates = pd.date_range(start=start_date, end=end_date, freq="D")
        rows = []
        for symbol in symbols:
            for d in dates:
                row = {"symbol": symbol, "date": d}
                for col in SENTIMENT_FEATURE_COLUMNS:
                    row[col] = 0.0
                rows.append(row)
        return pd.DataFrame(rows) if rows else pd.DataFrame()

    # ------------------------------------------------------------------
    # Redis cache (sync client, matching qlib-service pattern)
    # ------------------------------------------------------------------

    def _read_cache(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
    ) -> Optional[pd.DataFrame]:
        """Try to read cached sentiment features from Redis."""
        key = _build_cache_key(symbols, start_date, end_date)
        try:
            r = _get_redis_client()
            data = r.get(key)
            if data is None:
                return None
            unpacked = msgpack.unpackb(data, raw=False)
            df = pd.DataFrame(unpacked)
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
            return df
        except Exception as e:
            logger.debug("Redis cache read failed (non-fatal): %s", e)
            return None

    def _write_cache(
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
            r.setex(key, _CACHE_TTL, packed)
            logger.debug("Cached sentiment features: key=%s, rows=%d", key, len(df))
        except Exception as e:
            logger.debug("Redis cache write failed (non-fatal): %s", e)


# Module singleton
sentiment_service = SentimentService()
