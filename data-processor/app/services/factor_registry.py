"""Factor registry for RD-Agent discovered factors.

Manages the lifecycle of factors discovered by RD-Agent:
- Registration of new factors (expression, IC, ICIR, metadata)
- Activation/deactivation for production use
- Retrieval for feature_service integration

Factors are stored in the discovered_factors table and cached in Redis.
Active factors are automatically included in the feature matrix during
the next LightGBM training run.
"""

import json
import logging
from typing import Any, Optional

from app.core.settings_cache import settings_cache

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SQL queries (asyncpg parameterized: $1, $2, ...)
# ---------------------------------------------------------------------------

_SQL_UPSERT_FACTOR = """
INSERT INTO discovered_factors (
    name, expression, market, universe_id, ic, icir,
    discovery_round, is_active, metadata
) VALUES ($1, $2, $3, $4::uuid, $5, $6, $7, TRUE, $8::jsonb)
ON CONFLICT (expression, market) DO UPDATE SET
    ic = CASE WHEN ABS(EXCLUDED.ic) > ABS(discovered_factors.ic)
              THEN EXCLUDED.ic ELSE discovered_factors.ic END,
    icir = CASE WHEN ABS(EXCLUDED.ic) > ABS(discovered_factors.ic)
               THEN EXCLUDED.icir ELSE discovered_factors.icir END,
    discovery_round = CASE WHEN ABS(EXCLUDED.ic) > ABS(discovered_factors.ic)
                           THEN EXCLUDED.discovery_round
                           ELSE discovered_factors.discovery_round END,
    metadata = CASE WHEN ABS(EXCLUDED.ic) > ABS(discovered_factors.ic)
                    THEN EXCLUDED.metadata ELSE discovered_factors.metadata END
RETURNING id
"""

_SQL_GET_ACTIVE_FACTORS = """
SELECT id, name, expression, description, market, universe_id,
       ic, icir, discovery_round, is_active, metadata, created_at
FROM discovered_factors
WHERE market = $1 AND is_active = true
ORDER BY ABS(ic) DESC
"""

_SQL_GET_ALL_FACTORS = """
SELECT id, name, expression, description, market, universe_id,
       ic, icir, discovery_round, is_active, metadata, created_at
FROM discovered_factors
ORDER BY created_at DESC
"""

_SQL_GET_ALL_FACTORS_BY_MARKET = """
SELECT id, name, expression, description, market, universe_id,
       ic, icir, discovery_round, is_active, metadata, created_at
FROM discovered_factors
WHERE market = $1
ORDER BY created_at DESC
"""

_SQL_TOGGLE_FACTOR = """
UPDATE discovered_factors SET is_active = $1
WHERE id = $2::uuid
RETURNING id
"""


def _row_to_dict(row) -> dict[str, Any]:
    """Convert an asyncpg Record to a JSON-safe dict."""
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "expression": row["expression"],
        "description": row["description"],
        "market": row["market"],
        "universe_id": str(row["universe_id"]) if row["universe_id"] else None,
        "ic": float(row["ic"]) if row["ic"] is not None else None,
        "icir": float(row["icir"]) if row["icir"] is not None else None,
        "discovery_round": row["discovery_round"],
        "is_active": row["is_active"],
        "metadata": (
            json.loads(row["metadata"]) if isinstance(row["metadata"], str)
            else row["metadata"]
        ),
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


class FactorRegistry:
    """Registry for RD-Agent discovered factors.

    Uses the shared asyncpg pool from SettingsCache for all DB operations.
    All methods are async and safe for concurrent use.
    """

    async def register_factor(
        self,
        name: str,
        expression: str,
        market: str,
        universe_id: str | None,
        ic: float,
        icir: float,
        discovery_round: int,
        metadata: dict | None = None,
    ) -> str:
        """Register a single discovered factor.

        Inserts into discovered_factors with ON CONFLICT upsert:
        if the same (expression, market) already exists, updates IC/ICIR
        only if the new IC is better (higher absolute value).

        Args:
            name: Human-readable factor name.
            expression: Qlib expression string (e.g. "Mean($close,5)/$close").
            market: Market code (us, hk, cn).
            universe_id: UUID of the prediction universe, or None.
            ic: Information Coefficient for this factor.
            icir: IC Information Ratio.
            discovery_round: Which RD-Agent round discovered this.
            metadata: Optional extra metadata dict.

        Returns:
            Factor UUID string.
        """
        pool = settings_cache.pool
        if not pool:
            raise RuntimeError("SettingsCache pool not initialized")

        meta_json = json.dumps(metadata) if metadata else None

        async with pool.acquire(timeout=10) as conn:
            row = await conn.fetchrow(
                _SQL_UPSERT_FACTOR,
                name,
                expression,
                market,
                universe_id,
                ic,
                icir,
                discovery_round,
                meta_json,
            )

        factor_id = str(row["id"])
        logger.info(
            "Registered factor: name=%s, market=%s, ic=%.4f, icir=%.4f, id=%s",
            name, market, ic, icir, factor_id,
        )
        return factor_id

    async def get_active_factors(self, market: str) -> list[dict]:
        """Get all active factors for a market, ordered by |IC| descending.

        Args:
            market: Market code (us, hk, cn).

        Returns:
            List of factor dicts with id, name, expression, ic, icir, etc.
        """
        pool = settings_cache.pool
        if not pool:
            return []

        async with pool.acquire(timeout=10) as conn:
            rows = await conn.fetch(_SQL_GET_ACTIVE_FACTORS, market)

        return [_row_to_dict(r) for r in rows]

    async def get_all_factors(self, market: str | None = None) -> list[dict]:
        """Get all factors, optionally filtered by market.

        Args:
            market: If provided, filter to this market only.

        Returns:
            List of factor dicts ordered by created_at descending.
        """
        pool = settings_cache.pool
        if not pool:
            return []

        async with pool.acquire(timeout=10) as conn:
            if market:
                rows = await conn.fetch(_SQL_GET_ALL_FACTORS_BY_MARKET, market)
            else:
                rows = await conn.fetch(_SQL_GET_ALL_FACTORS)

        return [_row_to_dict(r) for r in rows]

    async def toggle_factor(self, factor_id: str, is_active: bool) -> bool:
        """Activate or deactivate a factor for production use.

        Args:
            factor_id: Factor UUID string.
            is_active: New activation state.

        Returns:
            True if factor was found and updated, False if not found.
        """
        pool = settings_cache.pool
        if not pool:
            return False

        async with pool.acquire(timeout=10) as conn:
            row = await conn.fetchrow(_SQL_TOGGLE_FACTOR, is_active, factor_id)

        if row:
            logger.info(
                "Factor %s set is_active=%s", factor_id, is_active,
            )
            return True

        logger.warning("Factor not found: %s", factor_id)
        return False

    async def register_batch(
        self,
        factors: list[dict],
        market: str,
        universe_id: str | None,
    ) -> int:
        """Register multiple factors from RD-Agent output.

        Each factor dict should have keys: name, expression, ic, icir,
        discovery_round, and optionally metadata.

        Args:
            factors: List of factor definition dicts.
            market: Market code for all factors in this batch.
            universe_id: Shared universe ID for all factors, or None.

        Returns:
            Count of successfully registered factors.
        """
        registered = 0
        for f in factors:
            try:
                await self.register_factor(
                    name=f["name"],
                    expression=f["expression"],
                    market=market,
                    universe_id=universe_id,
                    ic=float(f.get("ic", 0.0)),
                    icir=float(f.get("icir", 0.0)),
                    discovery_round=int(f.get("discovery_round", 0)),
                    metadata=f.get("metadata"),
                )
                registered += 1
            except Exception as e:
                logger.warning(
                    "Failed to register factor %s: %s",
                    f.get("name", "?"), e,
                )

        logger.info(
            "Batch registration complete: %d/%d factors for market=%s",
            registered, len(factors), market,
        )
        return registered


# Module singleton
factor_registry = FactorRegistry()
