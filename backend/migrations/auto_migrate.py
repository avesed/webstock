#!/usr/bin/env python3
"""Auto-migrate database schema on startup.

This script applies any pending SQL migrations to the database.
It's designed to be idempotent - safe to run multiple times.
"""

import os
import sys
import logging
from pathlib import Path

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [MIGRATE] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def parse_database_url(url: str) -> dict:
    """Parse DATABASE_URL into connection parameters."""
    # postgresql+asyncpg://user:pass@host:port/dbname
    # or postgresql://user:pass@host:port/dbname
    url = url.replace("postgresql+asyncpg://", "postgresql://")

    from urllib.parse import urlparse
    parsed = urlparse(url)

    return {
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "user": parsed.username,
        "password": parsed.password,
        "dbname": parsed.path.lstrip("/"),
    }


def get_applied_migrations(conn) -> set:
    """Get set of already applied migration names."""
    with conn.cursor() as cur:
        # Create migrations tracking table if not exists
        cur.execute("""
            CREATE TABLE IF NOT EXISTS _migrations (
                name VARCHAR(255) PRIMARY KEY,
                applied_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

        cur.execute("SELECT name FROM _migrations")
        return {row[0] for row in cur.fetchall()}


def apply_migration(conn, migration_path: Path) -> bool:
    """Apply a single migration file. Returns True if applied, False if skipped."""
    migration_name = migration_path.name

    with conn.cursor() as cur:
        try:
            # Read and execute SQL
            sql = migration_path.read_text()
            cur.execute(sql)

            # Record migration
            cur.execute(
                "INSERT INTO _migrations (name) VALUES (%s) ON CONFLICT DO NOTHING",
                (migration_name,)
            )
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to apply {migration_name}: {e}")
            raise


def main():
    """Run all pending migrations."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    # Find migrations directory
    migrations_dir = Path(__file__).parent
    if not migrations_dir.exists():
        logger.warning(f"Migrations directory not found: {migrations_dir}")
        return

    # Get all SQL migration files
    migration_files = sorted(migrations_dir.glob("*.sql"))
    if not migration_files:
        logger.info("No SQL migrations found")
        return

    # Connect to database
    conn_params = parse_database_url(database_url)
    logger.info(f"Connecting to database at {conn_params['host']}:{conn_params['port']}/{conn_params['dbname']}")

    try:
        conn = psycopg2.connect(**conn_params)
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    except Exception as e:
        logger.error(f"Failed to connect to database: {e}")
        sys.exit(1)

    try:
        # Get already applied migrations
        applied = get_applied_migrations(conn)
        logger.info(f"Found {len(applied)} previously applied migrations")

        # Apply pending migrations
        pending = [f for f in migration_files if f.name not in applied]
        if not pending:
            logger.info("No pending migrations")
            return

        logger.info(f"Applying {len(pending)} pending migrations...")
        for migration_file in pending:
            logger.info(f"  Applying: {migration_file.name}")
            apply_migration(conn, migration_file)
            logger.info(f"  ✓ Applied: {migration_file.name}")

        logger.info(f"Successfully applied {len(pending)} migrations")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
