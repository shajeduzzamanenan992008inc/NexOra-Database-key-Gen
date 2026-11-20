"""Regression tests for safe schema-readiness handling and atomic generation."""

from __future__ import annotations

import pytest


class FakePgError(Exception):
    """A pg8000-shaped exception carrying a SQLSTATE and server message."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__({"C": code, "M": message})


class BrokenProjectService:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def generate_project_key(self, **_kwargs):
        raise self.error


def install_error(app, monkeypatch, error: Exception) -> None:
    monkeypatch.setitem(app.extensions, "project_service", BrokenProjectService(error))


def generate(actor, name: str = "Schema Test"):
    return actor.post(
        "/api/v1/projects/generate-key",
        {"project_name": name, "label": ""},
    )


def test_missing_expiration_column_returns_safe_migration_503(
    app, owner, monkeypatch
):
    install_error(
        app,
        monkeypatch,
        FakePgError(
            "42703",
            'column "expiration_preset" of relation "reader_keys" does not exist',
        ),
    )

    response = generate(owner, "Missing Expiration Column")

    assert response.status_code == 503
    payload = response.get_json()
    error = payload["error"]
    assert error["code"] == "DATABASE_MIGRATION_REQUIRED"
    assert error["details"]["migration"] == "008_multi_key_expiration.sql"
    assert error["details"]["schema_ready"] is False
    assert error["request_id"]
    assert error["request_id"] == response.headers["X-Request-ID"]

    body = response.get_data(as_text=True)
    assert "expiration_preset" not in body
    assert "SELECT" not in body
    assert "postgresql://" not in body
    assert "Traceback" not in body
    assert "does not exist" not in body


def test_missing_007_column_is_also_classified_as_migration_required(
    app, owner, monkeypatch
):
    install_error(
        app,
        monkeypatch,
        FakePgError(
            "42703",
            'column "auto_delete_at" of relation "nexora_projects" does not exist',
        ),
    )

    response = generate(owner, "Missing Column")

    assert response.status_code == 503
    error = response.get_json()["error"]
    assert error["code"] == "DATABASE_MIGRATION_REQUIRED"
    assert error["details"]["migration"] == "007_project_key_management.sql"


def test_missing_function_42883_returns_safe_migration_503(app, owner, monkeypatch):
    install_error(
        app,
        monkeypatch,
        FakePgError(
            "42883",
            "function nexora_set_updated_at() does not exist",
        ),
    )

    response = generate(owner, "Missing Function")

    assert response.status_code == 503
    error = response.get_json()["error"]
    assert error["code"] == "DATABASE_MIGRATION_REQUIRED"
    assert error["details"]["migration"] == "006_rename_rh_function.sql"
    assert error["details"]["schema_ready"] is False

    body = response.get_data(as_text=True)
    assert "nexora_set_updated_at" not in body
    assert "42883" not in body
    assert "does not exist" not in body
    assert "CREATE FUNCTION" not in body


def test_invalid_schema_3f000_returns_safe_migration_503(app, owner, monkeypatch):
    install_error(
        app,
        monkeypatch,
        FakePgError("3F000", 'schema "public" does not exist'),
    )

    response = generate(owner, "Missing Schema")

    assert response.status_code == 503
    error = response.get_json()["error"]
    assert error["code"] == "DATABASE_MIGRATION_REQUIRED"
    assert error["details"]["migration"] == "001_initial_schema.sql"
    assert error["request_id"] == response.headers["X-Request-ID"]

    body = response.get_data(as_text=True)
    assert "3F000" not in body
    assert 'schema "public"' not in body
    assert "does not exist" not in body


def test_unrelated_database_error_is_not_misclassified(app, owner, monkeypatch):
    install_error(
        app,
        monkeypatch,
        FakePgError(
            "08006",
            'connection failure while reading relation "nexora_deleted_project_names"',
        ),
    )

    response = generate(owner, "Unrelated Failure")

    assert response.status_code == 500
    error = response.get_json()["error"]
    assert error["code"] == "internal_error"
    assert "details" not in error
    assert error["request_id"] == response.headers["X-Request-ID"]


def test_generation_transaction_rolls_back_when_key_insert_fails(app, owner):
    db = app.extensions["db"]
    db.execute(
        """
        CREATE OR REPLACE FUNCTION nexora_test_block_reader_key_insert()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'blocked for generation rollback test';
        END;
        $$
        """
    )
    db.execute(
        """
        CREATE TRIGGER trg_nexora_test_block_reader_key_insert
        BEFORE INSERT ON reader_keys
        FOR EACH ROW EXECUTE FUNCTION nexora_test_block_reader_key_insert()
        """
    )

    try:
        response = generate(owner, "Atomic Rollback Project")
        assert response.status_code == 500
        body = response.get_data(as_text=True)
        assert "plaintext_key" not in body
        assert "NXR-" not in body
    finally:
        db.execute(
            "DROP TRIGGER IF EXISTS trg_nexora_test_block_reader_key_insert "
            "ON reader_keys"
        )
        db.execute("DROP FUNCTION IF EXISTS nexora_test_block_reader_key_insert()")

    counts = db.fetch_one(
        """
        SELECT
          (SELECT count(*) FROM nexora_projects) AS projects,
          (SELECT count(*) FROM reader_keys) AS reader_keys
        """
    )
    assert counts == {"projects": 0, "reader_keys": 0}
