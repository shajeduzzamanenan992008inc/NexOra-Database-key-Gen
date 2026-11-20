"""Minimal, dependency-free SQL migration runner.

Splits PostgreSQL scripts without breaking dollar-quoted function bodies, skips
already-applied migrations by checksum, and applies each file in one
transaction. ``BEGIN``/``COMMIT`` lines inside files are handled by the runner.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Callable

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


MIGRATION_008 = "008_multi_key_expiration.sql"
MIGRATION_007 = "007_project_key_management.sql"
PROJECT_KEY_INDEX = "ix_reader_keys_project_created"
AUTO_DELETE_INDEX = "ix_nexora_projects_auto_delete"

# Browser-facing Supabase roles that must never hold privileges on reader_keys.
# ``PUBLIC`` is reported by ``aclexplode`` as grantee 0.
_FORBIDDEN_GRANTEES = frozenset({"public", "anon", "authenticated"})

# Privileges the platform service role needs to operate reader_keys. Only
# enforced when the ``service_role`` role actually exists in the database;
# local test clusters usually do not define it.
_SERVICE_ROLE_PRIVILEGES = frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"})

_REQUIRED_TABLES = (
    "admin_users",
    "nexora_projects",
    "reader_keys",
)

_REQUIRED_COLUMNS = (
    ("admin_users", "id"),
    ("nexora_projects", "auto_delete_at"),
    ("nexora_projects", "deleted_at"),
    ("reader_keys", "project_id"),
    ("reader_keys", "expiration_preset"),
    ("reader_keys", "is_permanent"),
    ("reader_keys", "expires_at"),
)

# label, constraint type, required normalized-definition fragments, name.
_REQUIRED_CONSTRAINTS = (
    (
        "reader_keys_expiration_preset_check",
        "c",
        ("expiration_preset", "1_day", "never"),
        "reader_keys_expiration_preset_check",
    ),
    (
        "reader_keys_expiration_state_check",
        "c",
        ("expiration_preset", "is_permanent", "expires_at"),
        "reader_keys_expiration_state_check",
    ),
)


def _check(
    name: str,
    ok: bool,
    detail: str = "",
    status: str | None = None,
) -> dict[str, object]:
    """Build one readiness check.

    ``status`` distinguishes a definite failure from metadata that could not
    be verified at all. Unverified checks still set ``ok`` to ``False`` so
    readiness remains fail-closed.
    """

    return {
        "name": name,
        "ok": bool(ok),
        "detail": detail,
        "status": status or ("pass" if ok else "fail"),
    }


def _normalize(text: object) -> str:
    """Lowercase and collapse whitespace for catalog text comparison."""

    return " ".join(str(text or "").lower().split())


def _safe_fetch(
    errors: list[str], label: str, fetch: Callable[[], object]
) -> object:
    try:
        return fetch()
    except Exception:
        # The exception text can contain SQL and credentials; only the stable
        # operation label is retained for readiness output.
        errors.append(label)
        return None


def collect_schema_facts(db: Database) -> tuple[dict[str, object], list[str]]:
    """Read the catalog facts Migration 008 readiness depends on.

    Every query is isolated so one failure cannot make the remaining checks
    appear healthy: unavailable metadata is returned as ``None`` and the
    operation label is recorded. Exception text is deliberately discarded
    because it may contain SQL or connection details.
    """

    errors: list[str] = []

    def table_exists() -> object:
        row = db.fetch_one(
            """
            SELECT
              to_regclass('public.admin_users') IS NOT NULL AS admin_users,
              to_regclass('public.nexora_projects') IS NOT NULL
                  AS nexora_projects,
              to_regclass('public.reader_keys') IS NOT NULL AS reader_keys
            """
        )
        if row is None:
            return None
        return {
            "admin_users": bool(row.get("admin_users")),
            "nexora_projects": bool(row.get("nexora_projects")),
            "reader_keys": bool(row.get("reader_keys")),
        }

    def columns() -> object:
        pairs = ", ".join(
            f"('{table}', '{column}')" for table, column in _REQUIRED_COLUMNS
        )
        rows = db.fetch_all(
            "SELECT table_name, column_name "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' "
            f"AND (table_name, column_name) IN ({pairs})"
        )
        found = {(row["table_name"], row["column_name"]) for row in rows}
        return {
            f"{table}.{column}": (table, column) in found
            for table, column in _REQUIRED_COLUMNS
        }

    def indexes() -> object:
        rows = db.fetch_all(
            """
            SELECT
              index_class.relname AS name,
              table_class.relname AS table_name,
              index_data.indisunique AS is_unique,
              index_data.indisvalid AS is_valid,
              index_data.indpred IS NOT NULL AS is_partial,
              pg_get_expr(index_data.indpred, index_data.indrelid)
                  AS predicate,
              ARRAY(
                  SELECT pg_get_indexdef(
                      index_data.indexrelid, key_no::int, true
                  )
                    FROM generate_series(1, index_data.indnkeyatts) AS key_no
                   ORDER BY key_no
              ) AS key_columns
            FROM pg_index AS index_data
            JOIN pg_class AS index_class
              ON index_class.oid = index_data.indexrelid
            JOIN pg_class AS table_class
              ON table_class.oid = index_data.indrelid
            JOIN pg_namespace AS namespace
              ON namespace.oid = index_class.relnamespace
            WHERE namespace.nspname = 'public'
              AND index_class.relname IN (
                  'ix_reader_keys_project_created',
                  'ix_nexora_projects_auto_delete'
              )
            """
        )
        facts: dict[str, dict[str, object] | None] = {
            PROJECT_KEY_INDEX: None,
            AUTO_DELETE_INDEX: None,
        }
        for row in rows:
            facts[row["name"]] = {
                "table": row["table_name"],
                "unique": bool(row["is_unique"]),
                "valid": bool(row["is_valid"]),
                "partial": bool(row["is_partial"]),
                "predicate": _normalize(row.get("predicate")),
                "columns": [
                    _normalize(column) for column in (row.get("key_columns") or [])
                ],
            }
        return facts

    def constraints() -> object:
        rows = db.fetch_all(
            "SELECT conname AS name, contype AS type, "
            "pg_get_constraintdef(oid) AS definition "
            "FROM pg_constraint "
            "WHERE conrelid = to_regclass('public.reader_keys') "
            "ORDER BY conname"
        )
        return [
            {
                "name": row["name"],
                "type": row["type"],
                "definition": _normalize(row.get("definition")),
            }
            for row in rows
        ]

    def rls() -> object:
        value = db.fetch_value(
            "SELECT relrowsecurity FROM pg_class "
            "WHERE oid = to_regclass('public.reader_keys')"
        )
        return None if value is None else bool(value)

    def roles() -> object:
        rows = db.fetch_all(
            "SELECT rolname FROM pg_roles "
            "WHERE rolname IN ('anon', 'authenticated', 'service_role')"
        )
        return {row["rolname"] for row in rows}

    def grants() -> object:
        rows = db.fetch_all(
            """
            SELECT
              CASE WHEN acl.grantee = 0 THEN 'PUBLIC'
                   ELSE pg_get_userbyid(acl.grantee) END AS grantee,
              acl.privilege_type AS privilege
            FROM pg_class AS table_class
            CROSS JOIN LATERAL aclexplode(
                COALESCE(
                    table_class.relacl,
                    acldefault('r', table_class.relowner)
                )
            ) AS acl
            WHERE table_class.oid =
                to_regclass('public.reader_keys')
            """
        )
        return [
            {
                "grantee": str(row["grantee"]),
                "privilege": str(row["privilege"]).upper(),
            }
            for row in rows
        ]

    facts = {
        "tables": _safe_fetch(errors, "table_metadata", table_exists),
        "columns": _safe_fetch(errors, "column_metadata", columns),
        "indexes": _safe_fetch(errors, "index_metadata", indexes),
        "constraints": _safe_fetch(
            errors, "constraint_metadata", constraints
        ),
        "rls_enabled": _safe_fetch(errors, "rls_metadata", rls),
        "roles": _safe_fetch(errors, "role_metadata", roles),
        "grants": _safe_fetch(errors, "privilege_metadata", grants),
    }
    return facts, errors


def _verify_index(
    row: dict[str, object] | None,
    *,
    table: str,
    columns: tuple[str, ...],
    unique: bool,
    partial: bool,
    predicate_terms: tuple[str, ...] = (),
) -> tuple[bool, str]:
    if row is None:
        return False, "index is missing"
    if row.get("table") != table:
        return False, f"index is attached to {row.get('table')}, not {table}"
    if not row.get("valid"):
        return False, "index is not valid"
    if bool(row.get("unique")) != unique:
        return False, "index uniqueness does not match the migration"
    if bool(row.get("partial")) != partial:
        return False, "index predicate presence does not match the migration"
    observed_columns = tuple(row.get("columns") or ())
    expected_columns = tuple(_normalize(column) for column in columns)
    if observed_columns != expected_columns:
        return False, (
            f"index columns are ({', '.join(observed_columns)}), "
            f"expected ({', '.join(expected_columns)})"
        )
    if predicate_terms:
        predicate = str(row.get("predicate") or "")
        missing = [term for term in predicate_terms if term not in predicate]
        if missing:
            return False, "index predicate does not match the migration"
    return True, ""


def _verify_constraints(
    rows: object,
) -> tuple[bool, str]:
    if rows is None:
        return False, "constraint metadata could not be confirmed"
    observed = list(rows)
    missing: list[str] = []
    for label, constraint_type, terms, name in _REQUIRED_CONSTRAINTS:
        matched = False
        for row in observed:
            if row.get("type") != constraint_type:
                continue
            if name is not None and row.get("name") != name:
                continue
            definition = str(row.get("definition") or "")
            if all(term in definition for term in terms):
                matched = True
                break
        if not matched:
            missing.append(label)
    if missing:
        return False, "missing or incorrect: " + ", ".join(missing)
    return True, ""


def evaluate_readiness(
    local: dict[str, str],
    known: dict[str, dict[str, object]],
    has_migration_table: bool | None,
    facts: dict[str, object],
    verification_errors: list[str] | tuple[str, ...] = (),
) -> list[dict[str, object]]:
    """Evaluate recorded migrations and catalog facts, failing closed.

    The function is pure so regression tests can supply catalog facts that
    describe a damaged schema without mutating a shared database.
    """

    checks: list[dict[str, object]] = []

    if has_migration_table is None:
        checks.append(
            _check(
                "schema_migrations_table",
                False,
                "schema_migrations could not be inspected",
            )
        )
    else:
        checks.append(
            _check(
                "schema_migrations_table",
                bool(has_migration_table),
                "" if has_migration_table else "schema_migrations is missing",
            )
        )

    checks.append(
        _check(
            "migration_008_file_present",
            MIGRATION_008 in local,
            "" if MIGRATION_008 in local else f"{MIGRATION_008} is missing locally",
        )
    )
    checks.append(
        _check(
            "migration_008_recorded",
            MIGRATION_008 in known,
            "" if MIGRATION_008 in known else f"{MIGRATION_008} is not recorded",
        )
    )
    checksum = local.get(MIGRATION_008)
    record = known.get(MIGRATION_008)
    if record is None:
        # There is nothing to compare against. This is not a mismatch, but it
        # must not be reported as a pass either.
        checks.append(
            _check(
                "migration_008_checksum",
                False,
                "not verified: no recorded checksum exists for "
                f"{MIGRATION_008}",
                status="not_verified",
            )
        )
    elif not checksum:
        checks.append(
            _check(
                "migration_008_checksum",
                False,
                "not verified: the local migration file is missing",
                status="not_verified",
            )
        )
    elif checksum == record.get("checksum"):
        checks.append(_check("migration_008_checksum", True, ""))
    else:
        checks.append(
            _check(
                "migration_008_checksum",
                False,
                "recorded checksum mismatch",
            )
        )

    pending = [name for name in local if name not in known]
    mismatches = [
        name
        for name in local
        if name in known and known[name].get("checksum") != local[name]
    ]
    checks.append(
        _check(
            "no_pending_migrations",
            not pending,
            "" if not pending else "pending: " + ", ".join(pending),
        )
    )
    checks.append(
        _check(
            "applied_checksums_match",
            not mismatches,
            "" if not mismatches else "mismatched: " + ", ".join(mismatches),
        )
    )

    tables = facts.get("tables")
    if tables is None:
        checks.append(
            _check("required_tables", False, "table metadata could not be confirmed")
        )
    else:
        missing_tables = [
            table for table in _REQUIRED_TABLES if not tables.get(table)
        ]
        checks.append(
            _check(
                "required_tables",
                not missing_tables,
                "" if not missing_tables else "missing: " + ", ".join(missing_tables),
            )
        )

    columns = facts.get("columns")
    if columns is None:
        checks.append(
            _check(
                "required_columns", False, "column metadata could not be confirmed"
            )
        )
    else:
        missing_columns = [name for name, present in columns.items() if not present]
        checks.append(
            _check(
                "required_columns",
                not missing_columns,
                ""
                if not missing_columns
                else "missing: " + ", ".join(sorted(missing_columns)),
            )
        )

    indexes = facts.get("indexes")
    if indexes is None:
        for name in (
            "multi_key_index_definition",
            "auto_delete_index_definition",
        ):
            checks.append(_check(name, False, "index metadata could not be confirmed"))
    else:
        ok, detail = _verify_index(
            indexes.get(PROJECT_KEY_INDEX),
            table="reader_keys",
            columns=("project_id", "created_at"),
            unique=False,
            partial=False,
        )
        checks.append(_check("multi_key_index_definition", ok, detail))

        ok, detail = _verify_index(
            indexes.get(AUTO_DELETE_INDEX),
            table="nexora_projects",
            columns=("auto_delete_at",),
            unique=False,
            partial=True,
            predicate_terms=("auto_delete_at is not null", "deleted_at is null"),
        )
        checks.append(_check("auto_delete_index_definition", ok, detail))

    ok, detail = _verify_constraints(facts.get("constraints"))
    checks.append(_check("expiration_constraints", ok, detail))

    rls_enabled = facts.get("rls_enabled")
    checks.append(
        _check(
            "reader_keys_rls_enabled",
            rls_enabled is True,
            "RLS could not be confirmed"
            if rls_enabled is None
            else ("" if rls_enabled else "row level security is disabled"),
        )
    )

    grants = facts.get("grants")
    if grants is None:
        checks.append(
            _check(
                "reader_keys_privileges",
                False,
                "table privileges could not be confirmed",
            )
        )
    else:
        unintended = sorted(
            {
                f"{str(grant.get('grantee')).lower()}:"
                f"{str(grant.get('privilege')).upper()}"
                for grant in grants
                if str(grant.get("grantee")).lower() in _FORBIDDEN_GRANTEES
            }
        )
        checks.append(
            _check(
                "reader_keys_privileges",
                not unintended,
                ""
                if not unintended
                else "unintended grants: " + ", ".join(unintended),
            )
        )

    roles = facts.get("roles")
    if roles is None:
        checks.append(
            _check(
                "service_role_grants", False, "role metadata could not be confirmed"
            )
        )
    elif "service_role" not in roles:
        checks.append(
            _check(
                "service_role_grants",
                True,
                "service_role role is not present; grants are not required",
            )
        )
    elif grants is None:
        checks.append(
            _check(
                "service_role_grants",
                False,
                "service_role grants could not be confirmed",
            )
        )
    else:
        granted = {
            str(grant.get("privilege")).upper()
            for grant in grants
            if str(grant.get("grantee")).lower() == "service_role"
        }
        missing_privileges = sorted(_SERVICE_ROLE_PRIVILEGES - granted)
        checks.append(
            _check(
                "service_role_grants",
                not missing_privileges,
                ""
                if not missing_privileges
                else "missing: " + ", ".join(missing_privileges),
            )
        )

    errors = list(verification_errors)
    checks.append(
        _check(
            "verification_queries",
            not errors,
            "" if not errors else "failed: " + ", ".join(errors),
        )
    )
    return checks


def inspect_migrations(db: Database, directory: Path) -> dict[str, object]:
    """Inspect migration state and Migration 007 readiness without writing.

    This is a read-only preflight. It intentionally does not create the
    ``schema_migrations`` table or execute any migration SQL. Readiness fails
    closed: an unavailable catalog query or unconfirmed security metadata is
    reported as not ready rather than being treated as healthy.
    """

    files = sorted(directory.glob("*.sql"))
    local: dict[str, str] = {}
    for path in files:
        local[path.name] = _checksum(path.read_text(encoding="utf-8"))

    errors: list[str] = []

    def migration_table() -> bool:
        return bool(
            db.fetch_value(
                "SELECT to_regclass('public.schema_migrations') IS NOT NULL"
            )
        )

    has_migration_table = _safe_fetch(
        errors, "schema_migrations_metadata", migration_table
    )
    rows: list[dict[str, object]] = []
    if has_migration_table:
        fetched = _safe_fetch(
            errors,
            "schema_migrations_rows",
            lambda: db.fetch_all(
                "SELECT filename, checksum, applied_at "
                "FROM schema_migrations ORDER BY filename"
            ),
        )
        if fetched is not None:
            rows = list(fetched)

    known = {row["filename"]: row for row in rows}
    pending = [name for name in local if name not in known]
    mismatches = [
        {
            "filename": name,
            "local": local[name],
            "database": known[name]["checksum"],
        }
        for name in local
        if name in known and known[name]["checksum"] != local[name]
    ]

    facts, fact_errors = collect_schema_facts(db)
    errors.extend(fact_errors)
    checks = evaluate_readiness(
        local, known, has_migration_table, facts, errors
    )

    tables = facts.get("tables") or {}
    columns = facts.get("columns") or {}
    indexes = facts.get("indexes") or {}

    def index_present(name: str) -> bool:
        row = indexes.get(name)
        return bool(row and row.get("valid"))

    objects = {
        "expiration_preset": bool(columns.get("reader_keys.expiration_preset")),
        "auto_delete_at": bool(columns.get("nexora_projects.auto_delete_at")),
        "multi_key_index": index_present(PROJECT_KEY_INDEX),
        "auto_delete_index": index_present(AUTO_DELETE_INDEX),
        "reader_keys_rls": facts.get("rls_enabled") is True,
    }

    migration_008 = {
        "present": MIGRATION_008 in local,
        "applied": MIGRATION_008 in known,
        "checksum": local.get(MIGRATION_008),
        "recorded_checksum": known.get(MIGRATION_008, {}).get("checksum"),
    }
    ready = bool(local) and all(bool(check["ok"]) for check in checks)

    return {
        "applied": rows,
        "pending": pending,
        "checksum_mismatches": mismatches,
        "objects": objects,
        "migration_008": migration_008,
        "checks": checks,
        "verification_errors": errors,
        "ready": ready,
    }


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
