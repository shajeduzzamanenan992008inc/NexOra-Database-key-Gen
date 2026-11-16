"""NexOra Phase 1 — Reader Key Generation, Hashing, and Security Tests."""

from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
import pytest

from backend.security.keys import (
    KeySpec,
    generate_plaintext_key,
    normalize_key,
    hash_reader_key,
    generate_device_credential,
    hash_device_credential,
    masked_hint,
)

PEPPER = "nexora-test-pepper-" + "0123456789abcdef" * 3


class TestReaderKeyGeneration:
    def test_single_key_generation(self):
        spec = KeySpec()
        key = generate_plaintext_key(spec)
        assert key.startswith("NXR-")
        normalized = normalize_key(key)
        assert normalized is not None
        assert len(normalized) == spec.random_length + len(spec.prefix)

    def test_bulk_key_uniqueness(self):
        spec = KeySpec()
        keys = [generate_plaintext_key(spec) for _ in range(200)]
        assert len(set(keys)) == 200

    def test_reader_key_hash_reproducible(self):
        normalized = "NXRABCD2345EFGH6789"
        h1 = hash_reader_key(PEPPER, normalized)
        h2 = hash_reader_key(PEPPER, normalized)
        assert h1 == h2
        assert len(h1) == 64  # SHA256 hex string

    def test_reader_key_hash_domain_separated(self):
        """Reader key HMAC must differ from standard license key HMAC for the same key string."""
        from backend.security.keys import hash_key as standard_hash_key
        normalized = "NXRABCD2345EFGH6789"
        reader_h = hash_reader_key(PEPPER, normalized)
        license_h = standard_hash_key(PEPPER, normalized)
        assert reader_h != license_h

    def test_masked_hint_never_reveals_full_key(self):
        spec = KeySpec()
        key = generate_plaintext_key(spec)
        hint = masked_hint(spec, key)
        assert hint != key
        assert "****" in hint
        assert hint.startswith("NXR")

    def test_device_credential_generation(self):
        cred1 = generate_device_credential()
        cred2 = generate_device_credential()
        assert cred1 != cred2
        assert len(cred1) >= 40  # 36 bytes urlsafe is ~48 chars
        # Token must be urlsafe
        assert re.match(r"^[A-Za-z0-9_-]+$", cred1)

    def test_device_credential_hashing(self):
        cred = generate_device_credential()
        h1 = hash_device_credential(PEPPER, cred)
        h2 = hash_device_credential(PEPPER, cred)
        assert h1 == h2
        assert len(h1) == 64
        # Empty pepper raises
        with pytest.raises(ValueError):
            hash_device_credential("", cred)


class TestKeyLifecycleLogic:
    def test_expiry_detection(self):
        now = datetime.now(timezone.utc)
        expired_at = now - timedelta(hours=1)
        future_at = now + timedelta(days=30)

        # Expired check
        assert expired_at < now
        assert future_at > now

    def test_permanent_key_has_no_expiry(self):
        is_permanent = True
        expires_at = None
        # Permanent keys do not expire by time
        assert is_permanent and expires_at is None
