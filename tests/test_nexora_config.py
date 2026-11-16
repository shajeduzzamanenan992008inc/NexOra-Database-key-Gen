"""NexOra Phase 1 — Configuration and Environment Security Tests."""

from __future__ import annotations

import pytest
from backend.config import ConfigError, Settings
from backend.security.passwords import hash_password

ADMIN_HASH = hash_password("Nx!ConfigTest2026")


def _valid_env() -> dict[str, str]:
    return {
        "DATABASE_URL": "postgresql://tester:secret@127.0.0.1:5432/nexora_test?sslmode=disable",
        "KEY_PEPPER": "nexora-test-pepper-" + "0123456789abcdef" * 3,
        "SECRET_KEY": "nexora-secret-key-" + "0123456789abcdef" * 3,
        "ADMIN_EMAIL": "admin@nexora.test",
        "ADMIN_PASSWORD_HASH": ADMIN_HASH,
    }


def _apply(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)


class TestNexOraConfiguration:
    def test_valid_env_loads_cleanly(self, monkeypatch):
        env = _valid_env()
        _apply(monkeypatch, env)
        settings = Settings.from_env()
        assert settings.admin_email == "admin@nexora.test"
        assert settings.api_version == "v1"
        assert settings.max_reader_installations_per_project == 1

    def test_secrets_excluded_from_repr(self, monkeypatch):
        env = _valid_env()
        env["SUPABASE_URL"] = "https://my-ref-1234.supabase.co"
        env["SUPABASE_SERVICE_ROLE_KEY"] = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.secret"
        _apply(monkeypatch, env)
        settings = Settings.from_env()

        rendered = repr(settings)
        assert env["SECRET_KEY"] not in rendered
        assert env["KEY_PEPPER"] not in rendered
        assert env["ADMIN_PASSWORD_HASH"] not in rendered
        assert env["SUPABASE_SERVICE_ROLE_KEY"] not in rendered
        assert "my-ref-1234.supabase.co" in rendered

    def test_supabase_url_must_start_with_https(self, monkeypatch):
        env = _valid_env()
        env["SUPABASE_URL"] = "http://my-ref.supabase.co"
        env["SUPABASE_SERVICE_ROLE_KEY"] = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.token"
        _apply(monkeypatch, env)

        with pytest.raises(ConfigError, match="must start with https://"):
            Settings.from_env()

    def test_supabase_url_must_be_supabase_domain(self, monkeypatch):
        env = _valid_env()
        env["SUPABASE_URL"] = "https://evil-phishing-site.org"
        env["SUPABASE_SERVICE_ROLE_KEY"] = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.token"
        _apply(monkeypatch, env)

        with pytest.raises(ConfigError, match="valid Supabase project URL"):
            Settings.from_env()

    def test_supabase_url_placeholder_rejected(self, monkeypatch):
        env = _valid_env()
        env["SUPABASE_URL"] = "https://REPLACE_PROJECT_REF.supabase.co"
        env["SUPABASE_SERVICE_ROLE_KEY"] = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.token"
        _apply(monkeypatch, env)

        with pytest.raises(ConfigError, match="placeholder"):
            Settings.from_env()

    def test_supabase_service_role_key_placeholder_rejected(self, monkeypatch):
        env = _valid_env()
        env["SUPABASE_URL"] = "https://project1234.supabase.co"
        env["SUPABASE_SERVICE_ROLE_KEY"] = "REPLACE_WITH_SUPABASE_SERVICE_ROLE_KEY"
        _apply(monkeypatch, env)

        with pytest.raises(ConfigError, match="placeholder"):
            Settings.from_env()

    def test_supabase_service_role_key_requires_url(self, monkeypatch):
        env = _valid_env()
        env["SUPABASE_SERVICE_ROLE_KEY"] = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.token"
        _apply(monkeypatch, env)

        with pytest.raises(ConfigError, match="SUPABASE_URL is required"):
            Settings.from_env()

    def test_supabase_url_requires_service_role_key(self, monkeypatch):
        env = _valid_env()
        env["SUPABASE_URL"] = "https://project1234.supabase.co"
        _apply(monkeypatch, env)

        with pytest.raises(ConfigError, match="SUPABASE_SERVICE_ROLE_KEY is required"):
            Settings.from_env()
