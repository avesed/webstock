"""News content storage service for file-based JSON storage."""

import json
import logging
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional
import uuid

logger = logging.getLogger(__name__)

# Base path for news content storage
BASE_PATH = Path("/app/data/news_content")


class NewsStorageService:
    """
    Service for storing and retrieving news full content as JSON files.

    File path format: data/news_content/YYYY/MM/DD/SYMBOL/news_id.json

    JSON content structure:
    {
        "news_id": "uuid",
        "symbol": "AAPL",
        "url": "https://...",
        "title": "...",
        "full_text": "...",
        "authors": ["Author Name"],
        "keywords": ["keyword1", "keyword2"],
        "top_image": "https://...",
        "language": "en",
        "fetched_at": "2024-01-01T00:00:00Z",
        "word_count": 1500,
        "metadata": {
            "source_domain": "example.com",
            "publish_date": "2024-01-01T00:00:00Z"
        }
    }
    """

    def __init__(self, base_path: Optional[Path] = None) -> None:
        """
        Initialize storage service.

        Args:
            base_path: Custom base path for storage. Defaults to data/news_content.
        """
        self.base_path = base_path or BASE_PATH
        self._ensure_base_dir()

    def _ensure_base_dir(self) -> None:
        """Ensure base directory exists."""
        try:
            self.base_path.mkdir(parents=True, exist_ok=True)
            logger.debug("News storage base directory ensured: %s", self.base_path)
        except Exception as e:
            logger.error("Failed to create base directory %s: %s", self.base_path, e)
            raise

    def _build_file_path(
        self,
        news_id: uuid.UUID,
        symbol: str,
        published_at: Optional[datetime] = None,
    ) -> Path:
        """
        Build file path for news content.

        Path format: YYYY/MM/DD/SYMBOL/news_id.json

        Args:
            news_id: News article UUID
            symbol: Stock symbol
            published_at: Publication date (defaults to now)

        Returns:
            Full path to the JSON file
        """
        date = published_at or datetime.now(timezone.utc)
        year = date.strftime("%Y")
        month = date.strftime("%m")
        day = date.strftime("%d")

        # Sanitize symbol for filesystem safety
        safe_symbol = "".join(c if c.isalnum() or c in ".-_" else "_" for c in symbol.upper())

        return self.base_path / year / month / day / safe_symbol / f"{news_id}.json"

    def save_content(
        self,
        news_id: uuid.UUID,
        symbol: str,
        content: Dict[str, Any],
        published_at: Optional[datetime] = None,
    ) -> str:
        """
        Save news content to JSON file.

        Args:
            news_id: News article UUID
            symbol: Stock symbol
            content: Content dictionary to save
            published_at: Publication date for path organization

        Returns:
            Relative file path (from base_path)

        Raises:
            IOError: If file writing fails
        """
        file_path = self._build_file_path(news_id, symbol, published_at)

        try:
            # Ensure directory exists
            file_path.parent.mkdir(parents=True, exist_ok=True)

            # Add metadata
            content["news_id"] = str(news_id)
            content["symbol"] = symbol
            content["saved_at"] = datetime.now(timezone.utc).isoformat()

            # Write JSON file with proper encoding
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(content, f, ensure_ascii=False, indent=2, default=str)

            # Return relative path
            relative_path = str(file_path.relative_to(self.base_path))
            logger.info(
                "Saved news content: news_id=%s, symbol=%s, path=%s",
                news_id, symbol, relative_path
            )
            return relative_path

        except Exception as e:
            logger.error(
                "Failed to save news content: news_id=%s, symbol=%s, error=%s",
                news_id, symbol, e
            )
            raise IOError(f"Failed to save news content: {e}") from e

    def read_content(self, file_path: str) -> Optional[Dict[str, Any]]:
        """
        Read news content from JSON file.

        Args:
            file_path: Relative path from base_path

        Returns:
            Content dictionary or None if not found
        """
        full_path = self.base_path / file_path

        try:
            if not full_path.exists():
                logger.warning("News content file not found: %s", file_path)
                return None

            with open(full_path, "r", encoding="utf-8") as f:
                content = json.load(f)

            logger.debug("Read news content from: %s", file_path)
            return content

        except json.JSONDecodeError as e:
            logger.error("Invalid JSON in news content file %s: %s", file_path, e)
            return None
        except Exception as e:
            logger.error("Failed to read news content %s: %s", file_path, e)
            return None

    def delete_content(self, file_path: str) -> bool:
        """
        Delete news content file.

        Args:
            file_path: Relative path from base_path

        Returns:
            True if deleted successfully, False otherwise
        """
        full_path = self.base_path / file_path

        try:
            if not full_path.exists():
                logger.debug("News content file already deleted: %s", file_path)
                return True

            full_path.unlink()
            logger.info("Deleted news content file: %s", file_path)

            # Clean up empty parent directories
            self._cleanup_empty_dirs(full_path.parent)

            return True

        except Exception as e:
            logger.error("Failed to delete news content %s: %s", file_path, e)
            return False

    def _cleanup_empty_dirs(self, dir_path: Path) -> None:
        """Remove empty directories up to base_path."""
        try:
            while dir_path != self.base_path and dir_path.is_dir():
                if any(dir_path.iterdir()):
                    break  # Directory not empty
                dir_path.rmdir()
                logger.debug("Removed empty directory: %s", dir_path)
                dir_path = dir_path.parent
        except Exception as e:
            logger.warning("Error cleaning up empty directories: %s", e)

    def cleanup_old_files(self, days: int = 30) -> int:
        """
        Delete news content files older than specified days.

        Args:
            days: Number of days to retain content

        Returns:
            Number of files deleted
        """
        if days <= 0:
            logger.warning("Invalid retention days: %d", days)
            return 0

        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
        cutoff_year = cutoff_date.year
        cutoff_month = cutoff_date.month
        cutoff_day = cutoff_date.day

        deleted_count = 0

        try:
            # Iterate through year/month/day directories
            for year_dir in self.base_path.iterdir():
                if not year_dir.is_dir() or not year_dir.name.isdigit():
                    continue

                year = int(year_dir.name)

                for month_dir in year_dir.iterdir():
                    if not month_dir.is_dir() or not month_dir.name.isdigit():
                        continue

                    month = int(month_dir.name)

                    for day_dir in month_dir.iterdir():
                        if not day_dir.is_dir() or not day_dir.name.isdigit():
                            continue

                        day = int(day_dir.name)

                        # Check if this date is before cutoff
                        try:
                            dir_date = datetime(year, month, day, tzinfo=timezone.utc)
                            if dir_date < cutoff_date:
                                # Delete entire day directory
                                file_count = sum(
                                    1 for _ in day_dir.rglob("*.json")
                                )
                                shutil.rmtree(day_dir)
                                deleted_count += file_count
                                logger.info(
                                    "Deleted old news directory: %s (%d files)",
                                    day_dir, file_count
                                )
                        except ValueError:
                            continue

                    # Clean up empty month directory
                    if month_dir.exists() and not any(month_dir.iterdir()):
                        month_dir.rmdir()

                # Clean up empty year directory
                if year_dir.exists() and not any(year_dir.iterdir()):
                    year_dir.rmdir()

            logger.info(
                "Cleanup completed: deleted %d files older than %d days",
                deleted_count, days
            )
            return deleted_count

        except Exception as e:
            logger.error("Error during cleanup: %s", e)
            return deleted_count

    def get_storage_stats(self) -> Dict[str, Any]:
        """
        Get storage statistics.

        Returns:
            Dictionary with storage statistics
        """
        try:
            total_files = 0
            total_size = 0
            oldest_file = None
            newest_file = None

            for json_file in self.base_path.rglob("*.json"):
                total_files += 1
                total_size += json_file.stat().st_size

                mtime = json_file.stat().st_mtime
                if oldest_file is None or mtime < oldest_file:
                    oldest_file = mtime
                if newest_file is None or mtime > newest_file:
                    newest_file = mtime

            return {
                "total_files": total_files,
                "total_size_bytes": total_size,
                "total_size_mb": round(total_size / (1024 * 1024), 2),
                "oldest_file": datetime.fromtimestamp(oldest_file, tz=timezone.utc).isoformat() if oldest_file else None,
                "newest_file": datetime.fromtimestamp(newest_file, tz=timezone.utc).isoformat() if newest_file else None,
                "base_path": str(self.base_path),
            }

        except Exception as e:
            logger.error("Error getting storage stats: %s", e)
            return {
                "error": str(e),
                "base_path": str(self.base_path),
            }


# Singleton instance
_storage_service: Optional[NewsStorageService] = None


def get_news_storage_service() -> NewsStorageService:
    """Get singleton NewsStorageService instance."""
    global _storage_service
    if _storage_service is None:
        _storage_service = NewsStorageService()
    return _storage_service
