"""Regression and Safety Tests for Phase 3 Step 4.4 Fixes.

Covers:
1. STEP 2: Database URL validation (contract, fail-closed, normalization, placeholder/postgres DSN rejection).
2. STEP 3: Expired key persistence and transaction safety.
3. STEP 4: Concurrent installation hardening, project row locking, and 409 unique violation conversion.
4. STEP 5: Reader key & installation lifecycle verification.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call
from uuid import uuid4
import pytest

from backend.errors import ApiError, ValidationError
from backend.services.project_service import ProjectService


@pytest.fixture
def mock_db():
    db = MagicMock()
    return db


@pytest.fixture
def mock_settings():
    settings = MagicMock()
    settings.key_pepper = "test-pepper-" + "0123456789abcdef" * 3
    settings.supabase_url = "https://verified-project.supabase.co"
    return settings


@pytest.fixture
def service(mock_db, mock_settings):
    return ProjectService(mock_db, mock_settings)


# ============================================================================
# STEP 2: Database URL Validation Tests
# ============================================================================

class TestDatabaseUrlValidation:
    """Verifies strict fail-closed validation of database_url contract."""

    def test_missing_server_supabase_url_fails_closed(self, mock_db, mock_settings):
        """If server-side SUPABASE_URL is missing/empty, activation must fail-closed with 503."""
        mock_settings.supabase_url = ""
        svc = ProjectService(mock_db, mock_settings)

        with pytest.raises(ApiError) as exc_info:
            svc.activate_reader(
                project_name="Test Project",
                database_url="https://verified-project.supabase.co",
                reader_key_plaintext="NXR-ABCD-EFGH-JKLM-NPQR",
            )
        assert exc_info.value.status == 503
        assert exc_info.value.code == "SERVER_CONFIGURATION_ERROR"

    def test_empty_submitted_database_url_rejected(self, service):
        """Empty or whitespace submitted URL must be rejected with ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            service.activate_reader(
                project_name="Test Project",
                database_url="",
                reader_key_plaintext="NXR-ABCD-EFGH-JKLM-NPQR",
            )
        assert "database_url" in exc_info.value.details.get("fields", {})

    def test_correct_configured_url_passes(self, service, mock_db):
        """Exact matching configured URL passes validation."""
        mock_db.fetch_one.return_value = {
            "id": uuid4(),
            "project_name": "Test Project",
            "status": "active",
            "deleted_at": None,
        }
        # Only testing URL validation phase by making subsequent project check pass
        # and checking that DATABASE_URL_MISMATCH is not raised.
        with pytest.raises(Exception) as exc_info:
            service.activate_reader(
                project_name="Test Project",
                database_url="https://verified-project.supabase.co",
                reader_key_plaintext="invalid-format-key",
            )
        # Should have passed URL validation and failed at key validation
        assert exc_info.value.code != "DATABASE_URL_MISMATCH"
        assert exc_info.value.code != "INVALID_DATABASE_URL"

    def test_url_with_trailing_slash_normalized(self, service, mock_db):
        """Trailing slashes must be safely stripped and normalized."""
        mock_db.fetch_one.return_value = {
            "id": uuid4(),
            "project_name": "Test Project",
            "status": "active",
            "deleted_at": None,
        }
        with pytest.raises(Exception) as exc_info:
            service.activate_reader(
                project_name="Test Project",
                database_url="https://verified-project.supabase.co///",
                reader_key_plaintext="invalid-key",
            )
        assert exc_info.value.code != "DATABASE_URL_MISMATCH"

    def test_incorrect_database_url_rejected(self, service):
        """Submitting an arbitrary/different Supabase URL raises 400 DATABASE_URL_MISMATCH."""
        with pytest.raises(ApiError) as exc_info:
            service.activate_reader(
                project_name="Test Project",
                database_url="https://malicious-phishing.supabase.co",
                reader_key_plaintext="NXR-ABCD-EFGH-JKLM-NPQR",
            )
        assert exc_info.value.status == 400
        assert exc_info.value.code == "DATABASE_URL_MISMATCH"

    def test_postgres_connection_string_rejected(self, service):
        """Attempt to submit postgresql:// or postgres:// connection string must be rejected."""
        with pytest.raises(ApiError) as exc_info:
            service.activate_reader(
                project_name="Test Project",
                database_url="postgresql://postgres:secretpassword@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres",
                reader_key_plaintext="NXR-ABCD-EFGH-JKLM-NPQR",
            )
        assert exc_info.value.status == 400
        assert exc_info.value.code == "INVALID_DATABASE_URL"
        assert "not a PostgreSQL connection string" in exc_info.value.message

    def test_placeholder_database_url_rejected(self, service):
        """Attempt to submit placeholder URL must be rejected."""
        placeholders = [
            "https://<your-project>.supabase.co",
            "https://replace-me.supabase.co",
            "https://example.supabase.co",
            "https://change-me.supabase.co",
        ]
        for ph in placeholders:
            with pytest.raises(ApiError) as exc_info:
                service.activate_reader(
                    project_name="Test Project",
                    database_url=ph,
                    reader_key_plaintext="NXR-ABCD-EFGH-JKLM-NPQR",
                )
            assert exc_info.value.status == 400
            assert exc_info.value.code == "DATABASE_URL_MISMATCH"


# ============================================================================
# STEP 3: Expired Key Persistence & Transaction Behavior Tests
# ============================================================================

class TestExpiredKeyBehavior:
    """Verifies that expired keys are safely updated to status='expired' and persisted."""

    def test_expired_key_persists_status_and_raises_410(self, service, mock_db):
        project_id = str(uuid4())
        key_id = str(uuid4())
        past_time = datetime.now(timezone.utc) - timedelta(days=2)

        # Mock project lookup
        mock_db.fetch_one.side_effect = [
            # 1. Project fetch
            {"id": project_id, "project_name": "Test Project", "status": "active", "deleted_at": None},
            # 2. Existing installation check (preliminary)
            None,
        ]

        # Mock cursor context
        mock_cursor = MagicMock()
        mock_db.cursor.return_value.__enter__.return_value = mock_cursor

        # In cursor:
        # 1. proj_lock
        # 2. installation re-check
        # 3. key_row lock
        mock_cursor.fetchone.side_effect = [
            (project_id, "active"),  # project lock
            None,                    # active installation check under lock
            (key_id, project_id, "generated", False, past_time),  # key row: expired!
        ]

        with pytest.raises(ApiError) as exc_info:
            service.activate_reader(
                project_name="Test Project",
                database_url="https://verified-project.supabase.co",
                reader_key_plaintext="NXR-ABCD-EFGH-JKLM-NPQR",
            )

        assert exc_info.value.status == 410
        assert exc_info.value.code == "KEY_EXPIRED"

        # Verify UPDATE reader_keys SET status = 'expired' was executed before commit
        update_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "UPDATE reader_keys SET status = 'expired'" in c[0][0]
        ]
        assert len(update_calls) == 1
        assert update_calls[0][0][1] == (key_id,)

        # Verify key was NOT marked 'used'
        used_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "status = 'used'" in c[0][0]
        ]
        assert len(used_calls) == 0

    def test_already_expired_key_immediately_rejected(self, service, mock_db):
        project_id = str(uuid4())
        key_id = str(uuid4())

        mock_db.fetch_one.side_effect = [
            {"id": project_id, "project_name": "Test Project", "status": "active", "deleted_at": None},
            None,
        ]

        mock_cursor = MagicMock()
        mock_db.cursor.return_value.__enter__.return_value = mock_cursor

        mock_cursor.fetchone.side_effect = [
            (project_id, "active"),
            None,
            (key_id, project_id, "expired", False, None),  # already marked expired
        ]

        with pytest.raises(ApiError) as exc_info:
            service.activate_reader(
                project_name="Test Project",
                database_url="https://verified-project.supabase.co",
                reader_key_plaintext="NXR-ABCD-EFGH-JKLM-NPQR",
            )

        assert exc_info.value.status == 410
        assert exc_info.value.code == "KEY_EXPIRED"


# ============================================================================
# STEP 4: Concurrent Installation Activation Hardening Tests
# ============================================================================

class TestConcurrentActivationHardening:
    """Verifies that project row locking and unique violation handling prevent duplicate active installations."""

    def test_locks_project_row_inside_transaction(self, service, mock_db):
        project_id = str(uuid4())
        key_id = str(uuid4())

        mock_db.fetch_one.side_effect = [
            {"id": project_id, "project_name": "Test Project", "status": "active", "deleted_at": None},
            None,
        ]

        mock_cursor = MagicMock()
        mock_db.cursor.return_value.__enter__.return_value = mock_cursor

        mock_cursor.fetchone.side_effect = [
            (project_id, "active"),  # project lock
            None,                    # installation check
            (key_id, project_id, "generated", True, None),  # permanent valid key
        ]

        result = service.activate_reader(
            project_name="Test Project",
            database_url="https://verified-project.supabase.co",
            reader_key_plaintext="NXR-ABCD-EFGH-JKLM-NPQR",
        )

        assert result["status"] == "activated"
        assert result["project_id"] == project_id

        # Verify SELECT ... FOR UPDATE was executed on nexora_projects
        proj_lock_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "FROM nexora_projects" in c[0][0] and "FOR UPDATE" in c[0][0]
        ]
        assert len(proj_lock_calls) == 1
        assert proj_lock_calls[0][0][1] == (project_id,)

    def test_active_installation_under_lock_raises_409(self, service, mock_db):
        project_id = str(uuid4())

        mock_db.fetch_one.side_effect = [
            {"id": project_id, "project_name": "Test Project", "status": "active", "deleted_at": None},
            None,  # preliminary check saw nothing
        ]

        mock_cursor = MagicMock()
        mock_db.cursor.return_value.__enter__.return_value = mock_cursor

        mock_cursor.fetchone.side_effect = [
            (project_id, "active"),  # project lock acquired
            ("existing-install-id",),  # re-check under lock finds installation created by concurrent request!
        ]

        with pytest.raises(ApiError) as exc_info:
            service.activate_reader(
                project_name="Test Project",
                database_url="https://verified-project.supabase.co",
                reader_key_plaintext="NXR-ABCD-EFGH-JKLM-NPQR",
            )

        assert exc_info.value.status == 409
        assert exc_info.value.code == "ACTIVE_INSTALLATION_EXISTS"

    def test_unique_violation_converted_to_clean_409(self, service, mock_db):
        """A PostgreSQL 23505 unique violation is converted to HTTP 409, not raw 500."""
        project_id = str(uuid4())
        key_id = str(uuid4())

        mock_db.fetch_one.side_effect = [
            {"id": project_id, "project_name": "Test Project", "status": "active", "deleted_at": None},
            None,
        ]

        # Simulate unique violation exception from pg8000
        class MockUniqueViolation(Exception):
            args = ({"C": "23505", "M": "duplicate key value violates unique constraint ux_reader_installations_project_active"},)

        mock_cursor = MagicMock()
        mock_db.cursor.return_value.__enter__.return_value = mock_cursor
        mock_cursor.fetchone.side_effect = [
            (project_id, "active"),
            None,
            (key_id, project_id, "generated", True, None),
        ]
        # Error raised when inserting installation
        mock_cursor.execute.side_effect = [
            None,  # lock proj
            None,  # check install
            None,  # lock key
            None,  # update key
            MockUniqueViolation("Unique violation"),  # insert installation fails on race
        ]

        with pytest.raises(ApiError) as exc_info:
            service.activate_reader(
                project_name="Test Project",
                database_url="https://verified-project.supabase.co",
                reader_key_plaintext="NXR-ABCD-EFGH-JKLM-NPQR",
            )

        assert exc_info.value.status == 409
        assert exc_info.value.code == "ACTIVE_INSTALLATION_EXISTS"


# ============================================================================
# STEP 5: Key Lifecycle and Authorization Tests
# ============================================================================

class TestKeyAndInstallationLifecycle:
    """Verifies complete Reader Key and Installation lifecycle constraints."""

    def test_already_used_key_cannot_be_redeemed(self, service, mock_db):
        project_id = str(uuid4())
        key_id = str(uuid4())

        mock_db.fetch_one.side_effect = [
            {"id": project_id, "project_name": "Test Project", "status": "active", "deleted_at": None},
            None,
        ]
        mock_cursor = MagicMock()
        mock_db.cursor.return_value.__enter__.return_value = mock_cursor
        mock_cursor.fetchone.side_effect = [
            (project_id, "active"),
            None,
            (key_id, project_id, "used", True, None),  # used key!
        ]

        with pytest.raises(ApiError) as exc_info:
            service.activate_reader(
                project_name="Test Project",
                database_url="https://verified-project.supabase.co",
                reader_key_plaintext="NXR-ABCD-EFGH-JKLM-NPQR",
            )

        assert exc_info.value.status == 410
        assert exc_info.value.code == "KEY_ALREADY_USED"

    def test_revoked_key_cannot_be_redeemed(self, service, mock_db):
        project_id = str(uuid4())
        key_id = str(uuid4())

        mock_db.fetch_one.side_effect = [
            {"id": project_id, "project_name": "Test Project", "status": "active", "deleted_at": None},
            None,
        ]
        mock_cursor = MagicMock()
        mock_db.cursor.return_value.__enter__.return_value = mock_cursor
        mock_cursor.fetchone.side_effect = [
            (project_id, "active"),
            None,
            (key_id, project_id, "revoked", True, None),  # revoked key!
        ]

        with pytest.raises(ApiError) as exc_info:
            service.activate_reader(
                project_name="Test Project",
                database_url="https://verified-project.supabase.co",
                reader_key_plaintext="NXR-ABCD-EFGH-JKLM-NPQR",
            )

        assert exc_info.value.status == 410
        assert exc_info.value.code == "KEY_REVOKED"

    def test_key_from_different_project_rejected(self, service, mock_db):
        project_id = str(uuid4())
        other_project_id = str(uuid4())
        key_id = str(uuid4())

        mock_db.fetch_one.side_effect = [
            {"id": project_id, "project_name": "Test Project", "status": "active", "deleted_at": None},
            None,
        ]
        mock_cursor = MagicMock()
        mock_db.cursor.return_value.__enter__.return_value = mock_cursor
        mock_cursor.fetchone.side_effect = [
            (project_id, "active"),
            None,
            (key_id, other_project_id, "generated", True, None),  # wrong project!
        ]

        with pytest.raises(ApiError) as exc_info:
            service.activate_reader(
                project_name="Test Project",
                database_url="https://verified-project.supabase.co",
                reader_key_plaintext="NXR-ABCD-EFGH-JKLM-NPQR",
            )

        assert exc_info.value.status == 403
        assert exc_info.value.code == "KEY_PROJECT_MISMATCH"

    def test_verify_reader_credential_valid(self, service, mock_db):
        mock_db.fetch_one.return_value = {
            "token_id": uuid4(),
            "installation_id": uuid4(),
            "project_id": uuid4(),
            "installation_status": "active",
            "project_name": "Test Project",
            "project_status": "active",
        }
        res = service.verify_reader_credential("valid-device-credential-token-123456789")
        assert res["project_name"] == "Test Project"

    def test_verify_reader_credential_revoked_installation(self, service, mock_db):
        mock_db.fetch_one.return_value = {
            "token_id": uuid4(),
            "installation_id": uuid4(),
            "project_id": uuid4(),
            "installation_status": "revoked",
            "project_name": "Test Project",
            "project_status": "active",
        }
        with pytest.raises(ApiError) as exc_info:
            service.verify_reader_credential("valid-device-credential-token-123456789")
        assert exc_info.value.status == 403
        assert exc_info.value.code == "INSTALLATION_REVOKED"

    def test_verify_reader_credential_inactive_project(self, service, mock_db):
        mock_db.fetch_one.return_value = {
            "token_id": uuid4(),
            "installation_id": uuid4(),
            "project_id": uuid4(),
            "installation_status": "active",
            "project_name": "Test Project",
            "project_status": "inactive",
        }
        with pytest.raises(ApiError) as exc_info:
            service.verify_reader_credential("valid-device-credential-token-123456789")
        assert exc_info.value.status == 403
        assert exc_info.value.code == "PROJECT_INACTIVE"

    def test_verify_reader_credential_unknown(self, service, mock_db):
        mock_db.fetch_one.return_value = None
        with pytest.raises(ApiError) as exc_info:
            service.verify_reader_credential("unknown-credential")
        assert exc_info.value.status == 401
        assert exc_info.value.code == "UNAUTHORIZED"
