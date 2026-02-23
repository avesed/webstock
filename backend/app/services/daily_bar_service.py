"""Service for querying daily OHLCV bars from PostgreSQL.

Collection and persistence are now handled by the data-service microservice
(Phase 7 migration). This module retains only the read-side query methods
used by internal.py and other backend consumers.
"""

import logging
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def _parse_bar_date(date_str: str) -> Optional[date]:
    """Parse a date from bar dict (e.g. '2025-01-15T00:00:00')."""
    try:
        return date.fromisoformat(date_str[:10])
    except (ValueError, TypeError):
        return None


class DailyBarService:
    """Query interface for stock_daily_bars table."""

    async def get_bars_batch(
        self,
        db: AsyncSession,
        symbols: list[str],
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict[str, dict[str, list]]:
        """Query bars from DB in columnar format for efficient DataFrame construction.

        Returns dict keyed by symbol, each containing lists of dates, open, high,
        low, close, volume.
        """
        if not symbols:
            return {}

        logger.info(
            "Querying bars: symbols=%d, start=%s, end=%s",
            len(symbols), start_date, end_date,
        )

        query = (
            "SELECT symbol, date, open, high, low, close, volume "
            "FROM stock_daily_bars "
            "WHERE symbol = ANY(:symbols)"
        )
        params: dict[str, Any] = {"symbols": symbols}

        if start_date is not None:
            query += " AND date >= :start_date"
            params["start_date"] = start_date
        if end_date is not None:
            query += " AND date <= :end_date"
            params["end_date"] = end_date

        query += " ORDER BY symbol, date"

        result = await db.execute(text(query), params)
        rows = result.fetchall()

        grouped: dict[str, dict[str, list]] = {}
        for row in rows:
            sym = row[0]
            if sym not in grouped:
                grouped[sym] = {
                    "dates": [],
                    "open": [],
                    "high": [],
                    "low": [],
                    "close": [],
                    "volume": [],
                }
            col = grouped[sym]
            col["dates"].append(str(row[1]))
            col["open"].append(float(row[2]) if isinstance(row[2], Decimal) else row[2])
            col["high"].append(float(row[3]) if isinstance(row[3], Decimal) else row[3])
            col["low"].append(float(row[4]) if isinstance(row[4], Decimal) else row[4])
            col["close"].append(float(row[5]) if isinstance(row[5], Decimal) else row[5])
            col["volume"].append(int(row[6]))

        logger.info(
            "Returned bars for %d/%d symbols, %d total rows",
            len(grouped), len(symbols), len(rows),
        )

        return grouped

    async def get_latest_dates(
        self,
        db: AsyncSession,
        market: str,
    ) -> dict[str, date]:
        """Get the latest data date per symbol for a given market."""
        result = await db.execute(
            text(
                "SELECT symbol, MAX(date) FROM stock_daily_bars "
                "WHERE market = :market GROUP BY symbol"
            ),
            {"market": market},
        )
        return {row[0]: row[1] for row in result.fetchall()}

    async def get_symbols_in_db(
        self,
        db: AsyncSession,
        market: str,
    ) -> list[str]:
        """Get all distinct symbols stored for a given market."""
        result = await db.execute(
            text(
                "SELECT DISTINCT symbol FROM stock_daily_bars "
                "WHERE market = :market"
            ),
            {"market": market},
        )
        return [row[0] for row in result.fetchall()]
