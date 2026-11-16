from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.config import ConfigError, DatabaseSettings, Settings
from backend.security.passwords import hash_password

ROOT = Path(__file__).resolve().parent.parent
ADMIN_HASH = hash_password("ConfigPolicy!2026Pass")


def _valid_env() -> dict[str, str]:
    return {
        "DATABASE_URL": "postgresql://tester:secret@127.0.0.1:5432/keygen_test?sslmode=disable",
        "KEY_PEPPER": "k" * 48,
        "SECRET_KEY": "s" * 48,
        "ADMIN_EMAIL": "admin@nexora.test",
        "ADMIN_PASSWORD_HASH": ADMIN_HASH,
    }


def _apply(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)


def test_required_environment_is_validated_and_secrets_stay_out_of_repr(monkeypatch):
    env = _valid_env()
    _apply(monkeypatch, env)

    settings = Settings.from_env()

    assert settings.admin_email == env["ADMIN_EMAIL"]
    assert settings.admin_role == "owner"
    assert settings.secret_key == env["SECRET_KEY"]
    rendered = repr(settings)
    assert env["SECRET_KEY"] not in rendered
    assert env["ADMIN_PASSWORD_HASH"] not in rendered
    assert env["KEY_PEPPER"] not in rendered


@pytest.mark.parametrize(
    "missing",
    [
        "DATABASE_URL",
        "KEY_PEPPER",
        "SECRET_KEY",
        "ADMIN_EMAIL",
        "ADMIN_PASSWORD_HASH",
    ],
)
def test_missing_required_environment_fails_closed(monkeypatch, missing):
    env = _valid_env()
    del env[missing]
    _apply(monkeypatch, env)
    monkeypatch.delenv(missing, raising=False)

    with pytest.raises(ConfigError) as error:
        Settings.from_env()

    assert missing in str(error.value)


def test_plaintext_admin_password_is_never_accepted_as_a_hash(monkeypatch):
    env = _valid_env()
    env["ADMIN_PASSWORD_HASH"] = "Sup3rSecret!Admin2026"
    _apply(monkeypatch, env)

    with pytest.raises(ConfigError, match="ADMIN_PASSWORD_HASH is invalid"):
        Settings.from_env()


def test_placeholder_admin_hash_and_secret_key_are_rejected(monkeypatch):
    env = _valid_env()
    env["ADMIN_PASSWORD_HASH"] = "REPLACE_WITH_ARGON2ID_PASSWORD_HASH"
    _apply(monkeypatch, env)
    with pytest.raises(ConfigError, match="ADMIN_PASSWORD_HASH is invalid"):
        Settings.from_env()

    env = _valid_env()
    env["SECRET_KEY"] = "REPLACE_WITH_AT_LEAST_32_RANDOM_CHARACTERS"
    _apply(monkeypatch, env)
    with pytest.raises(ConfigError, match="SECRET_KEY"):
        Settings.from_env()


def test_ssl_root_certificate_is_validated_and_resolved(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_SSLROOTCERT", raising=False)
    ca = tmp_path / "ca.crt"
    ca.write_text(
        "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n",
        encoding="ascii",
    )
    monkeypatch.setenv("DATABASE_SSLROOTCERT", str(ca))

    settings = DatabaseSettings.from_dsn(
        "postgresql://tester:secret@db.example.test:5432/keygen?sslmode=require"
    )

    assert settings.sslrootcert == str(ca.resolve())
    assert "ca.crt" not in settings.dsn_summary


def test_missing_ssl_root_certificate_fails_closed(monkeypatch):
    monkeypatch.delenv("DATABASE_SSLROOTCERT", raising=False)

    with pytest.raises(ConfigError, match="DATABASE_SSLROOTCERT"):
        DatabaseSettings.from_dsn(
            "postgresql://tester:secret@db.example.test:5432/keygen"
            "?sslmode=require&sslrootcert=certs/does-not-exist.crt"
        )


def test_env_template_contains_placeholders_only_and_env_is_ignored():
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "ADMIN_EMAIL=" in template
    assert "ADMIN_PASSWORD_HASH=REPLACE_WITH_ARGON2ID_PASSWORD_HASH" in template
    assert "SECRET_KEY=REPLACE_WITH_AT_LEAST_32_RANDOM_CHARACTERS" in template
    assert "DATABASE_URL=" in template
    assert not re.search(r"\$argon2id\$", template)

    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in [line.strip() for line in gitignore]
    assert "!.env.example" in [line.strip() for line in gitignore]
