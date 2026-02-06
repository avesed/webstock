"""Stock list service for local in-memory search optimization.

This service provides fast local search (<10ms) instead of slow API calls (500ms-2s).
Stock data is loaded from msgpack files and indexed for efficient prefix/ngram search.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import msgpack

logger = logging.getLogger(__name__)

# Default data directory relative to project root
DEFAULT_DATA_DIR = Path(__file__).parent.parent.parent.parent / "data" / "stock_list"


@dataclass
class LocalStock:
    """Stock data structure for local search."""

    symbol: str  # AAPL, 0700.HK, 600519.SS
    name: str  # Apple Inc.
    name_zh: str  # (optional)
    exchange: str  # NASDAQ, HKEX, SSE
    market: str  # us, hk, sh, sz, metal
    pinyin: str  # Complete pinyin (e.g., "PINGGUO")
    pinyin_initial: str  # Pinyin initials (e.g., "PG")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LocalStock":
        return cls(
            symbol=data.get("symbol", ""),
            name=data.get("name", ""),
            name_zh=data.get("name_zh", ""),
            exchange=data.get("exchange", ""),
            market=data.get("market", "us"),
            pinyin=data.get("pinyin", ""),
            pinyin_initial=data.get("pinyin_initial", ""),
        )


@dataclass
class SearchMatch:
    """Search result with match metadata."""

    stock: LocalStock
    score: float
    match_field: str  # Which field matched: symbol, name, name_zh, pinyin, pinyin_initial


class StockListService:
    """
    Stock list service for fast local search.

    Uses singleton pattern with async-safe initialization.
    Supports multiple index types for different search patterns:
    - symbol_prefix: "AA" -> {0, 15} (indexes of matching stocks)
    - name_prefix: "APP" -> {0}
    - name_zh_ngram: "" -> {0}, "" -> {0}
    - pinyin_full: "PING" -> {0}, "PINGGUO" -> {0}
    - pinyin_initial: "PG" -> {0}
    """

    _instance: Optional["StockListService"] = None
    _instance_lock = asyncio.Lock()

    def __init__(self, data_dir: Optional[Path] = None):
        """Initialize the service (private, use get_instance())."""
        self.data_dir = data_dir or DEFAULT_DATA_DIR
        self.stocks: List[LocalStock] = []

        # Index structures
        self.symbol_prefix: Dict[str, Set[int]] = {}
        self.name_prefix: Dict[str, Set[int]] = {}
        self.name_zh_ngram: Dict[str, Set[int]] = {}
        self.pinyin_full: Dict[str, Set[int]] = {}
        self.pinyin_initial: Dict[str, Set[int]] = {}

        # Market weight for sorting
        self.market_weights = {
            "us": 50,
            "hk": 40,
            "sh": 30,
            "sz": 30,
            "metal": 20,
        }

        self._loaded = False
        self._load_lock = asyncio.Lock()
        self._version: Optional[str] = None

    @classmethod
    async def get_instance(cls, data_dir: Optional[Path] = None) -> "StockListService":
        """Get singleton instance of the service."""
        if cls._instance is None:
            async with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls(data_dir)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton instance (for testing)."""
        cls._instance = None

    @property
    def is_loaded(self) -> bool:
        """Check if data is loaded."""
        return self._loaded and len(self.stocks) > 0

    @property
    def stock_count(self) -> int:
        """Get total number of loaded stocks."""
        return len(self.stocks)

    @property
    def version(self) -> Optional[str]:
        """Get current data version."""
        return self._version

    async def load(self, force: bool = False) -> bool:
        """
        Load stock data from msgpack file.

        Args:
            force: Force reload even if already loaded

        Returns:
            True if loaded successfully, False otherwise
        """
        if self._loaded and not force:
            logger.debug("Stock list already loaded, skipping")
            return True

        async with self._load_lock:
            # Double-check after acquiring lock
            if self._loaded and not force:
                return True

            stocks_file = self.data_dir / "stocks.msgpack"
            sha256_file = self.data_dir / "stocks.msgpack.sha256"
            version_file = self.data_dir / "version.json"

            if not stocks_file.exists():
                logger.warning(f"Stock list file not found: {stocks_file}")
                return False

            try:
                # Verify checksum if available
                if sha256_file.exists():
                    expected_hash = sha256_file.read_text().strip()
                    actual_hash = self._compute_sha256(stocks_file)
                    if expected_hash != actual_hash:
                        logger.error(
                            f"Stock list checksum mismatch: expected {expected_hash}, got {actual_hash}"
                        )
                        return False

                # Load stock data
                loop = asyncio.get_event_loop()
                data = await loop.run_in_executor(None, self._load_msgpack, stocks_file)

                if not data:
                    logger.warning("Empty stock list data")
                    return False

                # Convert to LocalStock objects
                stocks = [LocalStock.from_dict(item) for item in data]

                # Build indexes
                await loop.run_in_executor(None, self._build_index, stocks)

                # Update state
                self.stocks = stocks
                self._loaded = True

                # Load version info
                if version_file.exists():
                    version_data = json.loads(version_file.read_text())
                    self._version = version_data.get("version")

                logger.info(
                    f"Loaded {len(stocks)} stocks from {stocks_file}, version: {self._version}"
                )
                return True

            except Exception as e:
                logger.exception(f"Failed to load stock list: {e}")
                return False

    def _load_msgpack(self, file_path: Path) -> List[Dict[str, Any]]:
        """Load data from msgpack file (synchronous)."""
        with open(file_path, "rb") as f:
            return msgpack.unpack(f, raw=False)

    def _compute_sha256(self, file_path: Path) -> str:
        """Compute SHA256 hash of a file."""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256_hash.update(chunk)
        return sha256_hash.hexdigest()

    def _build_index(self, stocks: List[LocalStock]) -> None:
        """Build all search indexes (synchronous, run in executor)."""
        # Clear existing indexes
        self.symbol_prefix.clear()
        self.name_prefix.clear()
        self.name_zh_ngram.clear()
        self.pinyin_full.clear()
        self.pinyin_initial.clear()

        for idx, stock in enumerate(stocks):
            # Symbol prefix index (uppercase)
            symbol_upper = stock.symbol.upper()
            for i in range(1, len(symbol_upper) + 1):
                prefix = symbol_upper[:i]
                if prefix not in self.symbol_prefix:
                    self.symbol_prefix[prefix] = set()
                self.symbol_prefix[prefix].add(idx)

            # Name prefix index (lowercase for case-insensitive search)
            if stock.name:
                name_lower = stock.name.lower()
                # Index first 8 characters as prefixes for better substring matching
                for i in range(1, min(9, len(name_lower) + 1)):
                    prefix = name_lower[:i]
                    if prefix not in self.name_prefix:
                        self.name_prefix[prefix] = set()
                    self.name_prefix[prefix].add(idx)

            # Chinese name n-gram index (1-2 characters)
            if stock.name_zh:
                for i in range(len(stock.name_zh)):
                    # Single character
                    char = stock.name_zh[i]
                    if char not in self.name_zh_ngram:
                        self.name_zh_ngram[char] = set()
                    self.name_zh_ngram[char].add(idx)

                    # Bi-gram
                    if i < len(stock.name_zh) - 1:
                        bigram = stock.name_zh[i : i + 2]
                        if bigram not in self.name_zh_ngram:
                            self.name_zh_ngram[bigram] = set()
                        self.name_zh_ngram[bigram].add(idx)

            # Pinyin prefix index
            if stock.pinyin:
                pinyin_upper = stock.pinyin.upper()
                for i in range(1, min(10, len(pinyin_upper) + 1)):
                    prefix = pinyin_upper[:i]
                    if prefix not in self.pinyin_full:
                        self.pinyin_full[prefix] = set()
                    self.pinyin_full[prefix].add(idx)

            # Pinyin initial index
            if stock.pinyin_initial:
                initial_upper = stock.pinyin_initial.upper()
                for i in range(1, len(initial_upper) + 1):
                    prefix = initial_upper[:i]
                    if prefix not in self.pinyin_initial:
                        self.pinyin_initial[prefix] = set()
                    self.pinyin_initial[prefix].add(idx)

        logger.debug(
            f"Built indexes: symbol={len(self.symbol_prefix)}, "
            f"name={len(self.name_prefix)}, name_zh={len(self.name_zh_ngram)}, "
            f"pinyin={len(self.pinyin_full)}, initial={len(self.pinyin_initial)}"
        )

    def build_index(self, stocks: List[LocalStock]) -> None:
        """Public method to rebuild index (for testing or manual updates)."""
        self._build_index(stocks)
        self.stocks = stocks
        self._loaded = True

    def _contains_chinese(self, text: str) -> bool:
        """Check if text contains Chinese characters."""
        return bool(re.search(r"[\u4e00-\u9fff]", text))

    def search(
        self,
        query: str,
        markets: Optional[List[str]] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        Search stocks using local indexes.

        Args:
            query: Search query string
            markets: Optional list of markets to filter (us, hk, sh, sz, metal)
            limit: Maximum number of results to return

        Returns:
            List of search results with match_field indicating which field matched
        """
        if not self._loaded or not query:
            return []

        query = query.strip()
        if not query:
            return []

        # Normalize query
        query_upper = query.upper()
        query_lower = query.lower()

        # Track matches with scores
        matches: Dict[int, SearchMatch] = {}

        # 1. Exact symbol match (highest priority)
        for idx, stock in enumerate(self.stocks):
            if stock.symbol.upper() == query_upper:
                matches[idx] = SearchMatch(stock=stock, score=1000.0, match_field="symbol")

        # 2. Symbol prefix match
        if query_upper in self.symbol_prefix:
            for idx in self.symbol_prefix[query_upper]:
                if idx not in matches:
                    matches[idx] = SearchMatch(
                        stock=self.stocks[idx], score=500.0, match_field="symbol"
                    )

        # 3. English name prefix match (try progressively shorter prefixes)
        # For longer queries like "microsoft", check prefixes from 8 chars down to 2
        name_match_found = False
        for prefix_len in range(min(8, len(query_lower)), 1, -1):
            prefix = query_lower[:prefix_len]
            if prefix in self.name_prefix:
                for idx in self.name_prefix[prefix]:
                    if idx not in matches:
                        # Score based on how much of the query matched
                        score = 300.0 * (prefix_len / len(query_lower))
                        matches[idx] = SearchMatch(
                            stock=self.stocks[idx], score=score, match_field="name"
                        )
                    elif matches[idx].match_field != "name":
                        score = 300.0 * (prefix_len / len(query_lower))
                        if matches[idx].score < score:
                            matches[idx].score = score
                            matches[idx].match_field = "name"
                name_match_found = True
                break  # Use longest matching prefix

        # 4. Chinese name match (n-gram)
        if self._contains_chinese(query):
            # Try matching with the full query first
            if query in self.name_zh_ngram:
                for idx in self.name_zh_ngram[query]:
                    if idx not in matches:
                        matches[idx] = SearchMatch(
                            stock=self.stocks[idx], score=200.0, match_field="name_zh"
                        )
                    elif matches[idx].score < 200:
                        matches[idx].score = 200.0
                        matches[idx].match_field = "name_zh"
            else:
                # Try single character match
                for char in query:
                    if char in self.name_zh_ngram:
                        for idx in self.name_zh_ngram[char]:
                            if idx not in matches:
                                matches[idx] = SearchMatch(
                                    stock=self.stocks[idx],
                                    score=150.0,
                                    match_field="name_zh",
                                )
                            elif matches[idx].score < 150:
                                matches[idx].score = 150.0
                                matches[idx].match_field = "name_zh"

        # 5. Pinyin full match
        if query_upper in self.pinyin_full:
            for idx in self.pinyin_full[query_upper]:
                if idx not in matches:
                    matches[idx] = SearchMatch(
                        stock=self.stocks[idx], score=150.0, match_field="pinyin"
                    )
                elif matches[idx].score < 150:
                    matches[idx].score = 150.0
                    matches[idx].match_field = "pinyin"

        # 6. Pinyin initial match
        if query_upper in self.pinyin_initial:
            for idx in self.pinyin_initial[query_upper]:
                if idx not in matches:
                    matches[idx] = SearchMatch(
                        stock=self.stocks[idx], score=100.0, match_field="pinyin_initial"
                    )
                elif matches[idx].score < 100:
                    matches[idx].score = 100.0
                    matches[idx].match_field = "pinyin_initial"

        # Filter by markets if specified
        if markets:
            markets_lower = [m.lower() for m in markets]
            matches = {
                idx: match
                for idx, match in matches.items()
                if match.stock.market.lower() in markets_lower
            }

        # Add market weight bonus and sort
        results: List[Tuple[float, SearchMatch]] = []
        for idx, match in matches.items():
            market_weight = self.market_weights.get(match.stock.market.lower(), 0)
            final_score = match.score + market_weight
            results.append((final_score, match))

        # Sort by score descending
        results.sort(key=lambda x: x[0], reverse=True)

        # Return top results
        output = []
        for score, match in results[:limit]:
            result = match.stock.to_dict()
            result["match_field"] = match.match_field
            result["score"] = score
            output.append(result)

        return output

    async def reload(self) -> bool:
        """Reload data from disk (hot reload)."""
        return await self.load(force=True)

    def save(self, stocks: List[LocalStock]) -> bool:
        """
        Save stock data to msgpack file.

        Args:
            stocks: List of LocalStock objects to save

        Returns:
            True if saved successfully, False otherwise
        """
        try:
            # Ensure data directory exists
            self.data_dir.mkdir(parents=True, exist_ok=True)

            stocks_file = self.data_dir / "stocks.msgpack"
            sha256_file = self.data_dir / "stocks.msgpack.sha256"
            version_file = self.data_dir / "version.json"

            # Convert to dicts for serialization
            data = [stock.to_dict() for stock in stocks]

            # Save to msgpack
            with open(stocks_file, "wb") as f:
                msgpack.pack(data, f)

            # Compute and save checksum
            sha256_hash = self._compute_sha256(stocks_file)
            sha256_file.write_text(sha256_hash)

            # Save version info
            version_data = {
                "version": datetime.utcnow().strftime("%Y%m%d%H%M%S"),
                "updated_at": datetime.utcnow().isoformat(),
                "stock_count": len(stocks),
            }
            version_file.write_text(json.dumps(version_data, indent=2))

            logger.info(f"Saved {len(stocks)} stocks to {stocks_file}")
            return True

        except Exception as e:
            logger.exception(f"Failed to save stock list: {e}")
            return False

    def get_stats(self) -> Dict[str, Any]:
        """Get service statistics."""
        return {
            "loaded": self._loaded,
            "stock_count": len(self.stocks),
            "version": self._version,
            "index_sizes": {
                "symbol_prefix": len(self.symbol_prefix),
                "name_prefix": len(self.name_prefix),
                "name_zh_ngram": len(self.name_zh_ngram),
                "pinyin_full": len(self.pinyin_full),
                "pinyin_initial": len(self.pinyin_initial),
            },
            "market_counts": self._count_by_market(),
        }

    def _count_by_market(self) -> Dict[str, int]:
        """Count stocks by market."""
        counts: Dict[str, int] = {}
        for stock in self.stocks:
            market = stock.market.lower()
            counts[market] = counts.get(market, 0) + 1
        return counts


# Singleton getter function
_stock_list_service: Optional[StockListService] = None
_service_lock = asyncio.Lock()


async def get_stock_list_service(
    data_dir: Optional[Path] = None, auto_load: bool = True
) -> StockListService:
    """
    Get the singleton StockListService instance.

    Args:
        data_dir: Optional custom data directory
        auto_load: Whether to auto-load data on first access

    Returns:
        StockListService instance
    """
    global _stock_list_service

    if _stock_list_service is None:
        async with _service_lock:
            if _stock_list_service is None:
                _stock_list_service = await StockListService.get_instance(data_dir)
                if auto_load:
                    await _stock_list_service.load()

    return _stock_list_service


async def reset_stock_list_service() -> None:
    """Reset the singleton instance (for testing)."""
    global _stock_list_service
    StockListService.reset_instance()
    _stock_list_service = None
