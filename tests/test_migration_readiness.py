"""Tests for migration readiness and missing-schema classification."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from backend.migrations import (
    AUTO_DELETE_INDEX,
    MIGRATION_007,
    TOMBSTONE_INDEX,
    UNIQUE_PROJECT_KEY_INDEX,
    apply_migrations,
    collect_schema_facts,
    evaluate_readiness,
    inspect_migrations,
    split_sql,
)
from backend.schema_readiness import schema_readiness_issue

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = ROOT / "db" / "migrations"


class FakePgError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__({"C": code, "M": message})


def test_missing_relation_maps_to_007():
    issue = schema_readiness_issue(
        FakePgError(
            "42P01",
            'relation "nexora_deleted_project_names" does not exist',
        )
    )
    assert issue is not None
    assert issue.sqlstate == "42P01"
    assert issue.object_name == "nexora_deleted_project_names"
    assert issue.migration == MIGRATION_007


def test_missing_column_maps_to_007_before_its_relation():
    issue = schema_readiness_issue(
        FakePgError(
            "42703",
            'column "auto_delete_at" of relation "nexora_projects" does not exist',
        )
    )
    assert issue is not None
    assert issue.object_name == "auto_delete_at"
    assert issue.migration == MIGRATION_007


def test_unrelated_error_is_not_a_schema_issue():
    issue = schema_readiness_issue(
        FakePgError(
            "23505",
            'duplicate key value violates unique constraint "ux_reader_keys_key_hash"',
        )
    )
    assert issue is None


def test_migration_007_declares_all_application_requirements():
    text = (ROOT / "db" / "migrations" / MIGRATION_007).read_text(encoding="utf-8")

    for required in (
        "auto_delete_at",
        "ux_reader_keys_project_single",
        "ix_nexora_projects_auto_delete",
        "nexora_deleted_project_names",
        "ix_nexora_deleted_project_names_deleted_at",
        "ENABLE ROW LEVEL SECURITY",
        "GRANT SELECT, INSERT, UPDATE, DELETE",
    ):
        assert required in text

    statements = split_sql(text)
    assert len(statements) >= 8
    assert any("CREATE TABLE IF NOT EXISTS nexora_deleted_project_names" in s for s in statements)


def test_inspect_migrations_reports_migrated_test_database_ready(app):
    readiness = inspect_migrations(
        app.extensions["db"], ROOT / "db" / "migrations"
    )

    assert readiness["ready"] is True
    assert readiness["pending"] == []
    assert readiness["checksum_mismatches"] == []
    assert readiness["migration_007"]["present"] is True
    assert readiness["migration_007"]["applied"] is True
    assert all(readiness["objects"].values())
    assert readiness["verification_errors"] == []
    assert all(check["ok"] for check in readiness["checks"])


def test_missing_function_42883_maps_to_function_migration():
    issue = schema_readiness_issue(
        FakePgError(
            "42883",
            "function nexora_set_updated_at() does not exist",
        )
    )
    assert issue is not None
    assert issue.sqlstate == "42883"
    assert issue.object_name == "nexora_set_updated_at"
    assert issue.migration == "006_rename_rh_function.sql"


def test_invalid_schema_3f000_maps_to_baseline_migration():
    issue = schema_readiness_issue(
        FakePgError("3F000", 'schema "public" does not exist')
    )
    assert issue is not None
    assert issue.sqlstate == "3F000"
    assert issue.object_name == "public"
    assert issue.migration == "001_initial_schema.sql"


def _checks(facts, *, local=None, known=None, verification_errors=()):
    if local is None:
        local = {MIGRATION_007: "local-checksum"}
    if known is None:
        known = {
            MIGRATION_007: {
                "filename": MIGRATION_007,
                "checksum": "local-checksum",
            }
        }
    return {
        check["name"]: check
        for check in evaluate_readiness(
            local, known, True, facts, verification_errors
        )
    }


def _healthy_facts(app):
    facts, errors = collect_schema_facts(app.extensions["db"])
    assert errors == []
    return facts


def test_checksum_is_not_verified_without_a_recorded_migration(app):
    checks = _checks(_healthy_facts(app), known={})

    check = checks["migration_007_checksum"]
    assert check["ok"] is False
    assert check["status"] == "not_verified"
    assert "no recorded checksum" in check["detail"]
    assert "mismatch" not in check["detail"]

    # Independent checks keep their own outcomes.
    assert checks["migration_007_recorded"]["ok"] is False
    assert checks["no_pending_migrations"]["ok"] is False


def test_checksum_passes_when_recorded_checksum_matches(app):
    checks = _checks(
        _healthy_facts(app),
        local={MIGRATION_007: "same-checksum"},
        known={
            MIGRATION_007: {
                "filename": MIGRATION_007,
                "checksum": "same-checksum",
            }
        },
    )

    check = checks["migration_007_checksum"]
    assert check["ok"] is True
    assert check["status"] == "pass"
    assert check["detail"] == ""
    assert checks["migration_007_recorded"]["ok"] is True
    assert checks["no_pending_migrations"]["ok"] is True


def test_checksum_fails_on_recorded_mismatch(app):
    checks = _checks(
        _healthy_facts(app),
        local={MIGRATION_007: "local-checksum"},
        known={
            MIGRATION_007: {
                "filename": MIGRATION_007,
                "checksum": "database-checksum",
            }
        },
    )

    check = checks["migration_007_checksum"]
    assert check["ok"] is False
    assert check["status"] == "fail"
    assert check["detail"] == "recorded checksum mismatch"

    # A mismatch does not change the record or pending outcomes.
    assert checks["migration_007_recorded"]["ok"] is True
    assert checks["no_pending_migrations"]["ok"] is True


def test_missing_migration_record_keeps_readiness_not_ready(app):
    class NoMigrationRows:
        """Reads a healthy schema but reports an empty schema_migrations."""

        def __init__(self, db):
            self.db = db

        def fetch_value(self, sql, *args):
            if "schema_migrations" in sql:
                return True
            return self.db.fetch_value(sql, *args)

        def fetch_all(self, sql, *args):
            if "FROM schema_migrations" in sql:
                return []
            return self.db.fetch_all(sql, *args)

        def fetch_one(self, sql, *args):
            return self.db.fetch_one(sql, *args)

    readiness = inspect_migrations(
        NoMigrationRows(app.extensions["db"]), MIGRATIONS
    )
    checks = {check["name"]: check for check in readiness["checks"]}

    assert checks["migration_007_checksum"]["status"] == "not_verified"
    assert "mismatch" not in checks["migration_007_checksum"]["detail"]
    assert checks["migration_007_recorded"]["ok"] is False
    assert checks["no_pending_migrations"]["ok"] is False
    assert readiness["checksum_mismatches"] == []
    assert readiness["ready"] is False


def test_unique_index_must_target_reader_keys_project_id(app):
    facts = copy.deepcopy(_healthy_facts(app))
    facts["indexes"][UNIQUE_PROJECT_KEY_INDEX]["columns"] = [
        "project_id",
        "key_hash",
    ]

    checks = _checks(facts)

    assert checks["unique_index_definition"]["ok"] is False
    assert "project_id" in checks["unique_index_definition"]["detail"]


def test_non_unique_project_index_is_rejected(app):
    facts = copy.deepcopy(_healthy_facts(app))
    facts["indexes"][UNIQUE_PROJECT_KEY_INDEX]["unique"] = False

    checks = _checks(facts)

    assert checks["unique_index_definition"]["ok"] is False
    assert "uniqueness" in checks["unique_index_definition"]["detail"]


def test_auto_delete_index_must_keep_its_partial_predicate(app):
    facts = copy.deepcopy(_healthy_facts(app))
    facts["indexes"][AUTO_DELETE_INDEX]["predicate"] = "auto_delete_at is not null"

    checks = _checks(facts)

    assert checks["auto_delete_index_definition"]["ok"] is False


def test_tombstone_index_must_stay_on_the_tombstone_table(app):
    facts = copy.deepcopy(_healthy_facts(app))
    facts["indexes"][TOMBSTONE_INDEX]["table"] = "nexora_projects"

    checks = _checks(facts)

    assert checks["tombstone_index_definition"]["ok"] is False


def test_missing_tombstone_constraint_fails_readiness(app):
    facts = copy.deepcopy(_healthy_facts(app))
    facts["constraints"] = [
        row
        for row in facts["constraints"]
        if row["type"] != "f"
    ]

    checks = _checks(facts)

    assert checks["tombstone_constraints"]["ok"] is False
    assert "deleted_by_foreign_key_admin_users" in checks["tombstone_constraints"]["detail"]


def test_missing_service_role_grants_fail_when_role_exists(app):
    facts = copy.deepcopy(_healthy_facts(app))
    facts["roles"] = {"service_role", "anon", "authenticated"}
    facts["grants"] = [
        grant
        for grant in facts["grants"]
        if grant["grantee"].lower() != "service_role"
    ]

    checks = _checks(facts)

    assert checks["service_role_grants"]["ok"] is False
    assert "SELECT" in checks["service_role_grants"]["detail"]


def test_unintended_public_grant_fails_readiness(app):
    facts = copy.deepcopy(_healthy_facts(app))
    facts["grants"].append({"grantee": "PUBLIC", "privilege": "SELECT"})

    checks = _checks(facts)

    assert checks["tombstone_privileges"]["ok"] is False
    assert "public:SELECT" in checks["tombstone_privileges"]["detail"]


def test_unintended_anon_grant_fails_readiness(app):
    facts = copy.deepcopy(_healthy_facts(app))
    facts["grants"].append({"grantee": "anon", "privilege": "INSERT"})

    checks = _checks(facts)

    assert checks["tombstone_privileges"]["ok"] is False


def test_disabled_tombstone_rls_fails_readiness(app):
    facts = copy.deepcopy(_healthy_facts(app))
    facts["rls_enabled"] = False

    checks = _checks(facts)

    assert checks["tombstone_rls_enabled"]["ok"] is False
    assert "disabled" in checks["tombstone_rls_enabled"]["detail"]


def test_enabled_rls_but_unconfirmed_security_metadata_fails_closed(app):
    facts = copy.deepcopy(_healthy_facts(app))
    facts["grants"] = None
    facts["roles"] = None

    checks = _checks(facts)

    assert checks["tombstone_privileges"]["ok"] is False
    assert checks["service_role_grants"]["ok"] is False


def test_query_failure_fails_closed_without_leaking_sql():
    class BrokenDatabase:
        def fetch_value(self, *_args, **_kwargs):
            raise RuntimeError("password=secret SELECT * FROM private_table")

        def fetch_all(self, *_args, **_kwargs):
            raise RuntimeError("password=secret SELECT * FROM private_table")

        def fetch_one(self, *_args, **_kwargs):
            raise RuntimeError("password=secret SELECT * FROM private_table")

    readiness = inspect_migrations(BrokenDatabase(), MIGRATIONS)

    assert readiness["ready"] is False
    assert readiness["verification_errors"]
    assert any(
        not check["ok"] and check["name"] == "verification_queries"
        for check in readiness["checks"]
    )
    rendered = repr(readiness)
    assert "password=" not in rendered
    assert "SELECT" not in rendered
    assert "private_table" not in rendered


def test_migration_runner_rolls_back_a_failed_file(app, tmp_path):
    db = app.extensions["db"]
    directory = tmp_path / "migrations"
    directory.mkdir()
    filename = "900_migration_rollback_probe.sql"
    (directory / filename).write_text(
        "BEGIN;\n"
        "CREATE TABLE nexora_test_rollback_probe (id integer);\n"
        "SELECT 1 / 0;\n"
        "COMMIT;\n",
        encoding="utf-8",
    )

    with pytest.raises(Exception):
        apply_migrations(db, directory)

    assert db.fetch_value(
        "SELECT to_regclass('public.nexora_test_rollback_probe') IS NOT NULL"
    ) is False
    assert db.fetch_value(
        "SELECT count(*) FROM schema_migrations WHERE filename = %s",
        (filename,),
    ) == 0


def test_migration_runner_commits_a_valid_file_once(app, tmp_path):
    db = app.extensions["db"]
    directory = tmp_path / "migrations"
    directory.mkdir()
    filename = "901_migration_commit_probe.sql"
    (directory / filename).write_text(
        "BEGIN;\n"
        "CREATE TABLE nexora_test_commit_probe (id integer);\n"
        "COMMIT;\n",
        encoding="utf-8",
    )

    try:
        applied = apply_migrations(db, directory)
        assert applied == [filename]
        assert db.fetch_one(
            "SELECT filename FROM schema_migrations WHERE filename = %s",
            (filename,),
        ) == {"filename": filename}
        assert apply_migrations(db, directory) == []
    finally:
        db.execute("DROP TABLE IF EXISTS nexora_test_commit_probe")
        db.execute(
            "DELETE FROM schema_migrations WHERE filename = %s",
            (filename,),
        )
