"""Per-IP sliding-window rate limiting.

This limiter is intentionally dependency-free and applies to the API process.
With multiple worker processes each worker enforces its own window; the limits
below are conservative enough for that, and deployments that need a global
budget should put a shared limiter in front of the service (documented in
``docs/SECURITY.md``). Account lockout, which is stored in PostgreSQL, provides
the cross-worker defense for login attempts.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass

from flask import Request

from ..errors import ApiError
from ..validation import client_ip


@dataclass(frozen=True)
class RateRule:
    method: str
    pattern: re.Pattern[str]
    limit: int
    window_seconds: int
    name: str


_DEFAULT_LIMIT = 240
_DEFAULT_WINDOW = 60

RULES: tuple[RateRule, ...] = (
    RateRule("POST", re.compile(r"^/api/auth/login$"), 10, 60, "login"),
    RateRule("POST", re.compile(r"^/api/admin/login$"), 10, 60, "sms-login"),
    RateRule("POST", re.compile(r"^/api/keys/generate$"), 30, 60, "generate"),
    RateRule("POST", re.compile(r"^/api/keys/bulk-generate$"), 10, 60, "bulk-generate"),
    RateRule("POST", re.compile(r"^/api/keys/activate$"), 30, 60, "activate"),
    RateRule("POST", re.compile(r"^/api/keys/verify$"), 60, 60, "verify"),
    RateRule("POST", re.compile(r"^/api/keys/revoke$"), 30, 60, "revoke"),
    RateRule("POST", re.compile(r"^/api/keys/deactivate$"), 30, 60, "deactivate"),
    RateRule("POST", re.compile(r"^/api/keys/[^/]+/revoke$"), 30, 60, "sms-revoke"),
    RateRule("DELETE", re.compile(r"^/api/keys/[^/]+$"), 30, 60, "delete"),
    RateRule("GET", re.compile(r"^/api/keys/export$"), 10, 60, "export"),
    RateRule("POST", re.compile(r"^/api/reader/redeem$"), 20, 60, "sms-redeem"),
    RateRule(
        "POST", re.compile(r"^/api/reader/authenticate$"), 120, 60, "sms-authenticate"
    ),
    RateRule(
        "POST", re.compile(r"^/api/reader/heartbeat$"), 240, 60, "sms-heartbeat"
    ),
)


def _rule_for(method: str, path: str) -> RateRule:
    for rule in RULES:
        if rule.method == method and rule.pattern.match(path):
            return rule
    return RateRule(method, re.compile(r"^/api/"), _DEFAULT_LIMIT, _DEFAULT_WINDOW, "api")


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    def check(self, bucket: str, limit: int, window_seconds: int) -> int:
        """Register one hit. Returns 0 when allowed, else retry-after seconds."""
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            events = self._hits.setdefault(bucket, [])
            events[:] = [stamp for stamp in events if stamp > cutoff]
            if len(events) >= limit:
                retry_after = max(1, int(window_seconds - (now - events[0])) + 1)
                return retry_after
            events.append(now)

            if len(self._hits) > 5000:
                for key in list(self._hits):
                    if not self._hits[key]:
                        del self._hits[key]
            return 0


def enforce(
    request: Request,
    *,
    limiter: SlidingWindowLimiter,
    enabled: bool,
    trust_proxy: bool,
) -> None:
    if not enabled or request.method == "OPTIONS":
        return
    path = request.path.rstrip("/") or "/"
    if not path.startswith("/api/"):
        return
    rule = _rule_for(request.method, path)
    ip = client_ip(request, trust_proxy)
    retry_after = limiter.check(
        f"{rule.name}:{ip}", rule.limit, rule.window_seconds
    )
    if retry_after:
        raise ApiError(
            429,
            "rate_limited",
            "Too many requests. Wait a moment and try again.",
            {"retry_after_seconds": retry_after},
            {"Retry-After": str(retry_after)},
        )
