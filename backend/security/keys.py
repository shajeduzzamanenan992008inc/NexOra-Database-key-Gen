"""Cryptographically secure license key generation, normalization, and hashing.

Key material is generated with :mod:`secrets` (an OS CSPRNG). Only an
HMAC-SHA256 digest keyed by the server-side ``KEY_PEPPER`` is stored. Because
license keys must be looked up by value, a deterministic keyed digest (not a
password hash) is the correct construction: it is indexed, it cannot be
reversed without the pepper, and the 80-bit default entropy makes guessing
infeasible even if the database leaks.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import re
import secrets
from dataclasses import dataclass

# 32 characters: no I, O, 0 or 1, so keys are safe to read aloud and type.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ALPHABET_SIZE = len(ALPHABET)
SEPARATORS = ("-", "_", "")
MIN_GROUPS = 1
MAX_GROUPS = 8
MIN_GROUP_LENGTH = 3
MAX_GROUP_LENGTH = 10
MIN_RANDOM_LENGTH = 12
MAX_RANDOM_LENGTH = 64
_TOKEN_RE = re.compile(r"^[A-Z0-9]{12,96}$")
_LABEL_RE = re.compile(r"^[A-Z0-9]{0,12}$")


class KeyFormatError(ValueError):
    """The requested or supplied key format is invalid."""


@dataclass(frozen=True)
class KeySpec:
    """Format description for a batch of keys.

    The default produces ``NXR-XXXX-XXXX-XXXX-XXXX`` (80 bits of entropy).
    """

    prefix: str = "NXR"
    suffix: str = ""
    groups: int = 4
    group_length: int = 4
    separator: str = "-"

    def __post_init__(self) -> None:
        prefix = (self.prefix or "").strip().upper()
        suffix = (self.suffix or "").strip().upper()
        if not _LABEL_RE.match(prefix):
            raise KeyFormatError(
                "Prefix must be 0-12 characters using A-Z and 0-9."
            )
        if not _LABEL_RE.match(suffix):
            raise KeyFormatError(
                "Suffix must be 0-12 characters using A-Z and 0-9."
            )
        if not MIN_GROUPS <= self.groups <= MAX_GROUPS:
            raise KeyFormatError(f"Groups must be between {MIN_GROUPS} and {MAX_GROUPS}.")
        if not MIN_GROUP_LENGTH <= self.group_length <= MAX_GROUP_LENGTH:
            raise KeyFormatError(
                f"Group length must be between {MIN_GROUP_LENGTH} and {MAX_GROUP_LENGTH}."
            )
        if self.separator not in SEPARATORS:
            raise KeyFormatError("Separator must be '-', '_' or empty.")
        random_length = self.random_length
        if not MIN_RANDOM_LENGTH <= random_length <= MAX_RANDOM_LENGTH:
            raise KeyFormatError(
                "Total random characters must be between "
                f"{MIN_RANDOM_LENGTH} and {MAX_RANDOM_LENGTH}; "
                "increase groups or group length."
            )
        if len(self.prefix) + len(self.suffix) > 24:
            raise KeyFormatError("Prefix and suffix together must be 24 characters or fewer.")
        object.__setattr__(self, "prefix", prefix)
        object.__setattr__(self, "suffix", suffix)

    @property
    def random_length(self) -> int:
        return self.groups * self.group_length

    @property
    def entropy_bits(self) -> float:
        return round(self.random_length * math.log2(ALPHABET_SIZE), 1)

    def groups_of(self, random_text: str) -> list[str]:
        length = self.group_length
        return [random_text[index * length : (index + 1) * length] for index in range(self.groups)]

    def render(self, random_text: str) -> str:
        parts: list[str] = []
        if self.prefix:
            parts.append(self.prefix)
        parts.extend(self.groups_of(random_text))
        if self.suffix:
            parts.append(self.suffix)
        return self.separator.join(parts)


def generate_plaintext_key(spec: KeySpec) -> str:
    """Generate one key with the OS CSPRNG and render it in display format."""
    random_text = "".join(
        secrets.choice(ALPHABET) for _ in range(spec.random_length)
    )
    return spec.render(random_text)


def normalize_key(value: str) -> str | None:
    """Return the canonical lookup form, or ``None`` when the input is malformed.

    Separators, underscores and whitespace are ignored so a user can paste the
    key with or without dashes.
    """
    if not isinstance(value, str):
        return None
    normalized = re.sub(r"[\s\-_]", "", value).upper()
    if not _TOKEN_RE.match(normalized):
        return None
    return normalized


# Frozen cryptographic domain separator. Changing this string changes every
# stored HMAC and would invalidate all existing license keys and identifier
# hashes, so it intentionally keeps its original value across product renames.
_DOMAIN_PREFIX = "rh-esports:v1"


def hash_key(pepper: str, normalized_key: str) -> str:
    """HMAC-SHA256 digest used for indexed lookup and storage."""
    if not pepper:
        raise ValueError("A key pepper is required.")
    digest = hmac.new(
        pepper.encode("utf-8"),
        f"{_DOMAIN_PREFIX}:license-key:{normalized_key}".encode("utf-8"),
        hashlib.sha256,
    )
    return digest.hexdigest()


def hash_identifier(pepper: str, purpose: str, value: str) -> str:
    """Keyed digest for device/account identifiers and activation IPs."""
    digest = hmac.new(
        pepper.encode("utf-8"),
        f"{_DOMAIN_PREFIX}:{purpose}:{value.strip().lower()}".encode("utf-8"),
        hashlib.sha256,
    )
    return digest.hexdigest()


def fingerprint(value: str, visible: int = 4) -> str:
    """A short non-reversible display hint such as ``...9f2c``."""
    cleaned = (value or "").strip()
    if not cleaned:
        return ""
    return "..." + cleaned[-visible:]


def key_template(spec: KeySpec) -> str:
    """Masked template, e.g. ``NXR-****-****-****-****``."""
    parts: list[str] = []
    if spec.prefix:
        parts.append(spec.prefix)
    parts.extend("*" * spec.group_length for _ in range(spec.groups))
    if spec.suffix:
        parts.append(spec.suffix)
    return spec.separator.join(parts)


def masked_hint(spec: KeySpec, plaintext_key: str) -> str:
    """Display hint revealing only the final characters of the last group."""
    normalized = normalize_key(plaintext_key)
    if normalized is None:
        raise KeyFormatError("Cannot mask a malformed key.")

    body = normalized
    if spec.prefix:
        body = body[len(spec.prefix) :]
    if spec.suffix:
        body = body[: len(body) - len(spec.suffix)]
    groups = spec.groups_of(body)

    parts: list[str] = []
    if spec.prefix:
        parts.append(spec.prefix)
    parts.extend("*" * spec.group_length for _ in range(len(groups) - 1))
    last = groups[-1]
    parts.append(last[-min(4, len(last)) :])
    if spec.suffix:
        parts.append(spec.suffix)
    return spec.separator.join(parts)
