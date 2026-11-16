"""Classify PostgreSQL schema-readiness failures.

The application must never present a missing migration as an unexplained
generic HTTP 500. This module recognizes PostgreSQL's missing-relation,
missing-column, missing-function and missing-schema SQLSTATEs and maps known
application objects to the migration that creates them.

Only SQLSTATE and the object name are inspected. Query text, connection
details, credentials and stack traces are never returned to the browser.
"""

from __future__ import annotations

from dataclasses import dataclass

from .db import sqlstate

MIGRATION_007 = "007_project_key_management.sql"

# Missing-schema states that can be caused by an unapplied or incomplete
# migration. Normal data errors (for example 23505 unique violations) are not
# included and must retain their existing behavior.
_MISSING_SCHEMA_STATES = frozenset({"42P01", "42703", "42883", "3F000"})

# Insertion order matters: a column error can also mention its relation, so
# the more specific object must be checked before the table name.
_MIGRATION_BY_OBJECT = {
    "nexora_deleted_project_names": MIGRATION_007,
    "auto_delete_at": MIGRATION_007,
    "ux_reader_keys_project_single": MIGRATION_007,
    "ix_nexora_projects_auto_delete": MIGRATION_007,
    "ix_nexora_deleted_project_names_deleted_at": MIGRATION_007,
    "nexora_projects": "005_nexora_projects.sql",
    "reader_keys": "005_nexora_projects.sql",
    "reader_installations": "005_nexora_projects.sql",
    "device_tokens": "005_nexora_projects.sql",
    "system_settings": "005_nexora_projects.sql",
    "nexora_set_updated_at": "006_rename_rh_function.sql",
    "admin_users": "001_initial_schema.sql",
    "admin_sessions": "001_initial_schema.sql",
    "admin_audit_logs": "001_initial_schema.sql",
    # SQLSTATE 3F000 (invalid_schema_name). A missing ``public`` schema means
    # the baseline has not been provisioned, so point at the first migration.
    "public": "001_initial_schema.sql",
}


@dataclass(frozen=True)
class SchemaReadinessIssue:
    """A missing-schema failure with only safe, non-credential metadata."""

    sqlstate: str
    object_name: str | None
    migration: str | None


def _pg_error_message(exc: BaseException) -> str:
    for arg in exc.args:
        if isinstance(arg, dict):
            message = arg.get("M") or arg.get("message")
            if isinstance(message, str):
                return message
    return str(exc)


def schema_readiness_issue(exc: BaseException) -> SchemaReadinessIssue | None:
    """Return a schema issue for known missing-schema errors, otherwise None.

    The classification is intentionally based on SQLSTATE first. An unrelated
    database error is never converted into a migration error merely because its
    message contains a table name.
    """

    state = sqlstate(exc)
    if state not in _MISSING_SCHEMA_STATES:
        return None

    message = _pg_error_message(exc).lower()
    object_name = None
    for candidate in _MIGRATION_BY_OBJECT:
        if f'"{candidate.lower()}"' in message or candidate.lower() in message:
            object_name = candidate
            break

    return SchemaReadinessIssue(
        sqlstate=state,
        object_name=object_name,
        migration=_MIGRATION_BY_OBJECT.get(object_name) if object_name else None,
    )
