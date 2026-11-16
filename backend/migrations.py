"""Minimal, dependency-free SQL migration runner.

Splits PostgreSQL scripts without breaking dollar-quoted function bodies, skips
already-applied migrations by checksum, and applies each file in one
transaction. ``BEGIN``/``COMMIT`` lines inside files are handled by the runner.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .db import Database

_MIGRATION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   text PRIMARY KEY,
    checksum   text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""

_DOLLAR_TAG = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$")


def split_sql(sql: str) -> list[str]:
    statements: list[str] = []
    buffer: list[str] = []
    index = 0
    length = len(sql)

    while index < length:
        char = sql[index]
        pair = sql[index : index + 2]

        if pair == "--":
            newline = sql.find("\n", index)
            index = length if newline == -1 else newline + 1
            buffer.append("\n")
            continue

        if pair == "/*":
            end = sql.find("*/", index + 2)
            index = length if end == -1 else end + 2
            buffer.append(" ")
            continue

        if char in {"'", '"'}:
            quote = char
            buffer.append(char)
            index += 1
            while index < length:
                current = sql[index]
                buffer.append(current)
                if current == quote:
                    if index + 1 < length and sql[index + 1] == quote:
                        buffer.append(sql[index + 1])
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            continue

        if char == "$":
            match = _DOLLAR_TAG.match(sql, index)
            if match:
                tag = match.group(0)
                end = sql.find(tag, match.end())
                if end == -1:
                    raise ValueError("Unterminated dollar-quoted string in migration.")
                buffer.append(sql[index : end + len(tag)])
                index = end + len(tag)
                continue

        if char == ";":
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
            index += 1
            continue

        buffer.append(char)
        index += 1

    trailing = "".join(buffer).strip()
    if trailing:
        statements.append(trailing)

    return [
        statement
        for statement in statements
        if statement.strip().upper() not in {"BEGIN", "COMMIT"}
    ]


def _checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def apply_migrations(db: Database, directory: Path) -> list[str]:
    """Apply pending ``*.sql`` migrations in filename order."""
    files = sorted(directory.glob("*.sql"))
    if not files:
        return []

    with db.cursor() as cursor:
        cursor.execute(_MIGRATION_TABLE)

    applied: list[str] = []
    rows = db.fetch_all("SELECT filename, checksum FROM schema_migrations")
    known = {row["filename"]: row["checksum"] for row in rows}

    for path in files:
        text = path.read_text(encoding="utf-8")
        digest = _checksum(text)
        if path.name in known:
            if known[path.name] != digest:
                raise RuntimeError(
                    f"Migration {path.name} was modified after it was applied."
                )
            continue

        statements = split_sql(text)
        with db.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
            cursor.execute(
                "INSERT INTO schema_migrations (filename, checksum) VALUES (%s, %s)",
                (path.name, digest),
            )
        applied.append(path.name)

    return applied
