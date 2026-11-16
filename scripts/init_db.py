#!/usr/bin/env python
"""Apply database migrations.

Usage:
    python scripts/init_db.py
    DATABASE_URL=postgresql://... python scripts/init_db.py
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.config import ConfigError, DatabaseSettings  # noqa: E402
from backend.db import Database, DatabaseUnavailable  # noqa: E402
from backend.migrations import apply_migrations  # noqa: E402

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply NexOra database migrations.")
    parser.add_argument("--database-url", help="Override DATABASE_URL.")
    parser.add_argument(
        "--migrations",
        default=str(ROOT / "db" / "migrations"),
        help="Directory containing .sql migrations.",
    )
    args = parser.parse_args()

    if load_dotenv is not None:
        load_dotenv(ROOT / ".env")

    dsn = args.database_url or os.getenv("DATABASE_URL")
    try:
        settings = DatabaseSettings.from_dsn(dsn, pool_max=2)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    db = Database(settings)
    try:
        applied = apply_migrations(db, Path(args.migrations))
    except DatabaseUnavailable as exc:
        print(f"Database error: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()

    if applied:
        print("Applied migrations:")
        for name in applied:
            print(f"  - {name}")
    else:
        print("Database is up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
