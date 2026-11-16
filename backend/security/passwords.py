"""Password hashing and the administrator password policy.

New password hashes use Argon2id (memory-hard, the current OWASP recommendation
for passwords). Legacy ``scrypt`` hashes produced by earlier releases remain
verifiable, so upgrading the application does not invalidate an existing
administrator account. Plaintext passwords are never stored or logged.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from functools import lru_cache

from argon2 import PasswordHasher, Type, extract_parameters

# OWASP Argon2id baseline: 64 MiB, 3 iterations, 4 lanes.
_PASSWORD_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=64 * 1024,
    parallelism=4,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)

# Legacy scrypt parameters retained for verifying hashes created before the
# Argon2id migration.
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SCRYPT_MAXMEM = 64 * 1024 * 1024
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128

# Bounds applied before verification so a corrupted or attacker-supplied hash
# row cannot force an unbounded memory allocation (availability protection).
_ARGON2_MIN_MEMORY = 8 * 1024
_ARGON2_MAX_MEMORY = 1024 * 1024
_ARGON2_MAX_TIME = 10
_ARGON2_MAX_PARALLELISM = 16
_SCRYPT_MIN_N = 2**14
_SCRYPT_MAX_N = 2**20
_SCRYPT_MAX_R = 32
_SCRYPT_MAX_P = 16

_WEAK_PASSWORDS = frozenset(
    {
        "password1234",
        "passwordpassword",
        "administrator",
        "administrator1",
        "changeme1234",
        "letmein123456",
        "qwertyuiop123",
        "123456789012",
        "nexora-admin",
    }
)


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _scrypt_verify(password: str, encoded: str) -> bool:
    """Verify a legacy scrypt hash. Malformed hashes fail closed."""
    try:
        scheme, n, r, p, salt_b64, digest_b64 = encoded.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64, validate=True)
        expected = base64.b64decode(digest_b64, validate=True)
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
            maxmem=SCRYPT_MAXMEM,
        )
    except Exception:
        return False
    return hmac.compare_digest(candidate, expected)


def _check_argon2_parameters(encoded: str) -> None:
    """Reject malformed or unreasonably expensive Argon2 hashes."""
    try:
        parameters = extract_parameters(encoded)
    except Exception:
        raise ValueError("the Argon2 hash is malformed") from None
    if parameters.type is not Type.ID:
        raise ValueError("only Argon2id hashes are accepted")
    if not _ARGON2_MIN_MEMORY <= parameters.memory_cost <= _ARGON2_MAX_MEMORY:
        raise ValueError("the Argon2 memory cost is outside the accepted range")
    if not 1 <= parameters.time_cost <= _ARGON2_MAX_TIME:
        raise ValueError("the Argon2 time cost is outside the accepted range")
    if not 1 <= parameters.parallelism <= _ARGON2_MAX_PARALLELISM:
        raise ValueError("the Argon2 parallelism is outside the accepted range")


def hash_password(password: str) -> str:
    """Hash a password with Argon2id and a fresh random salt."""
    if not isinstance(password, str) or not password:
        raise ValueError("Password must be a non-empty string.")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must be no more than {MAX_PASSWORD_LENGTH} characters."
        )
    return _PASSWORD_HASHER.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    """Verify an Argon2id or legacy scrypt hash in constant time.

    Unknown or malformed hash formats fail closed rather than raising.
    """
    if not isinstance(password, str) or not isinstance(encoded, str) or not encoded:
        return False
    if encoded.startswith("$argon2"):
        try:
            _check_argon2_parameters(encoded)
            return _PASSWORD_HASHER.verify(encoded, password)
        except Exception:
            return False
    if encoded.startswith("scrypt$"):
        return _scrypt_verify(password, encoded)
    return False


def validate_password_hash(encoded: str) -> None:
    """Validate the structure of a password hash supplied by configuration.

    No password is available here, so this only checks the encoded form: the
    algorithm must be Argon2id or a well-formed legacy scrypt hash. Raises
    ``ValueError`` with a safe message when the value is unusable.
    """
    if not isinstance(encoded, str) or not encoded:
        raise ValueError("the hash is empty")
    if encoded.startswith("$argon2"):
        _check_argon2_parameters(encoded)
        return
    if encoded.startswith("scrypt$"):
        try:
            scheme, n, r, p, salt_b64, digest_b64 = encoded.split("$")
            if scheme != "scrypt":
                raise ValueError
            n_value, r_value, p_value = int(n), int(r), int(p)
            salt = base64.b64decode(salt_b64, validate=True)
            digest = base64.b64decode(digest_b64, validate=True)
        except Exception:
            raise ValueError("the scrypt hash is malformed") from None
        if (
            not _SCRYPT_MIN_N <= n_value <= _SCRYPT_MAX_N
            or n_value & (n_value - 1)
            or not 1 <= r_value <= _SCRYPT_MAX_R
            or not 1 <= p_value <= _SCRYPT_MAX_P
            or not 8 <= len(salt) <= 64
            or not 16 <= len(digest) <= 64
        ):
            raise ValueError("the scrypt hash parameters are invalid")
        return
    raise ValueError("the hash must be Argon2id (preferred) or legacy scrypt")


@lru_cache(maxsize=1)
def dummy_password_hash() -> str:
    """A fixed-format hash used to equalize timing for unknown accounts."""
    return hash_password(secrets.token_urlsafe(32))


def password_problems(password: str, email: str = "") -> list[str]:
    """Return human-readable policy violations (empty when acceptable)."""
    problems: list[str] = []
    if not isinstance(password, str):
        return ["Password must be text."]
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"Use at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        problems.append(f"Use no more than {MAX_PASSWORD_LENGTH} characters.")
    if password.lower() in _WEAK_PASSWORDS:
        problems.append("This password is too common.")
    if email:
        local = email.split("@", 1)[0].strip().lower()
        if len(local) >= 3 and local in password.lower():
            problems.append("Do not include your email name in the password.")

    classes = 0
    if any(char.islower() for char in password):
        classes += 1
    if any(char.isupper() for char in password):
        classes += 1
    if any(char.isdigit() for char in password):
        classes += 1
    if any(not char.isalnum() for char in password):
        classes += 1
    if classes < 3:
        problems.append(
            "Use at least three of: lowercase, uppercase, digits, symbols."
        )
    return problems
