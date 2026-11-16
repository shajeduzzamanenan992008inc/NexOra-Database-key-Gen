from __future__ import annotations

import base64
import hashlib
import re
import secrets

import pytest

from backend.migrations import split_sql
from backend.security.keys import (
    ALPHABET,
    KeyFormatError,
    KeySpec,
    generate_plaintext_key,
    hash_identifier,
    hash_key,
    key_template,
    masked_hint,
    normalize_key,
)
from backend.security.passwords import (
    hash_password,
    password_problems,
    verify_password,
)

PEPPER = "unit-test-pepper-" + "x" * 40


def test_default_spec_is_eighty_bit_key_with_safe_alphabet():
    spec = KeySpec()
    assert spec.random_length == 16
    assert spec.entropy_bits == 80.0
    key = generate_plaintext_key(spec)
    assert re.fullmatch(r"NXR-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}", key)
    body = key.removeprefix("NXR-").replace("-", "")
    assert set(body) <= set(ALPHABET)
    assert not set("IO01") & set(body)


def test_generated_keys_are_unique():
    spec = KeySpec(groups=4, group_length=5)
    keys = {generate_plaintext_key(spec) for _ in range(2000)}
    assert len(keys) == 2000


def test_custom_prefix_suffix_and_separator():
    spec = KeySpec(prefix="OMEGA", suffix="END", groups=3, group_length=5, separator="_")
    key = generate_plaintext_key(spec)
    assert key.startswith("OMEGA_")
    assert key.endswith("_END")
    assert key.count("_") == 4
    assert key_template(spec) == "OMEGA_" + "_".join(["*****"] * 3) + "_END"


def test_normalization_accepts_spacing_and_case():
    spec = KeySpec()
    key = generate_plaintext_key(spec)
    normalized = normalize_key(key)
    assert normalized is not None
    assert normalize_key(key.lower().replace("-", " ").replace(" ", "_")) == normalized
    assert normalize_key("short") is None
    assert normalize_key("NXR-!!!!-!!!!-!!!!-!!!!") is None
    assert normalize_key(123) is None


def test_hash_is_deterministic_and_pepper_sensitive():
    key = normalize_key(generate_plaintext_key(KeySpec()))
    first = hash_key(PEPPER, key)
    second = hash_key(PEPPER, key)
    other = hash_key(PEPPER + "-different", key)
    assert first == second
    assert first != other
    assert len(first) == 64
    assert re.fullmatch(r"[0-9a-f]{64}", first)


def test_identifier_hashes_are_purpose_separated():
    device = hash_identifier(PEPPER, "device", "Device-ABC-1234")
    same = hash_identifier(PEPPER, "device", "device-abc-1234")
    account = hash_identifier(PEPPER, "account", "Device-ABC-1234")
    assert device == same
    assert device != account


def test_masked_hint_reveals_only_last_group_tail():
    spec = KeySpec(groups=4, group_length=4)
    key = generate_plaintext_key(spec)
    hint = masked_hint(spec, key)
    assert hint.startswith("NXR-****-****-****-")
    assert hint.endswith(key[-4:])
    assert key not in hint


@pytest.mark.parametrize(
    "kwargs",
    [
        {"groups": 0},
        {"groups": 9},
        {"group_length": 2},
        {"group_length": 11},
        {"groups": 2, "group_length": 3},
        {"prefix": "BAD!"},
        {"suffix": "x" * 13},
        {"separator": "/"},
    ],
)
def test_invalid_specs_are_rejected(kwargs):
    with pytest.raises(KeyFormatError):
        KeySpec(**kwargs)


def test_spec_uppercases_labels():
    spec = KeySpec(prefix="nxr", suffix="end")
    assert spec.prefix == "NXR"
    assert spec.suffix == "END"


def test_argon2id_password_hash_round_trip():
    encoded = hash_password("CorrectHorse!2026")
    assert encoded.startswith("$argon2id$")
    assert "CorrectHorse!2026" not in encoded
    assert verify_password("CorrectHorse!2026", encoded)
    assert not verify_password("wrong-password", encoded)
    assert not verify_password("CorrectHorse!2026", "not-a-hash")
    assert hash_password("CorrectHorse!2026") != encoded


def test_legacy_scrypt_hashes_remain_verifiable():
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        b"CorrectHorse!2026",
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=32,
        maxmem=32 * 1024 * 1024,
    )
    encoded = "scrypt$16384$8$1${}${}".format(
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )
    assert verify_password("CorrectHorse!2026", encoded)
    assert not verify_password("wrong-password", encoded)


def test_password_policy_rejects_weak_values():
    assert password_problems("short")
    assert password_problems("alllowercase123")
    assert password_problems("Nx!KeyGen2026Pass", "nexorakeygen2026@example.com") == []
    problems = password_problems("Nova!Blitz2026", "owner@example.com")
    assert problems == []


def test_split_sql_handles_dollar_quotes_comments_and_transactions():
    script = """
    BEGIN;
    -- a semicolon in a comment ;
    CREATE FUNCTION f() RETURNS integer AS $$
    BEGIN
        RETURN 1;
    END;
    $$ LANGUAGE plpgsql;
    /* block comment ; */
    CREATE TABLE x (id integer);
    COMMIT;
    """
    statements = split_sql(script)
    assert len(statements) == 2
    assert statements[0].startswith("CREATE FUNCTION")
    assert "RETURN 1;" in statements[0]
    assert statements[1].startswith("CREATE TABLE")
