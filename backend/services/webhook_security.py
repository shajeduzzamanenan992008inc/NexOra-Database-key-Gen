"""SSRF-safe webhook destination validation and pinned delivery.

The original webhook path validated a hostname with ``socket.getaddrinfo`` and
then let ``urllib`` resolve it a second time. A hostile DNS server could return
a public address for the validation lookup and a private address for the
delivery lookup (DNS rebinding). This module removes that gap:

* every resolved address must be globally routable;
* the HTTP connection is opened to the validated IP address, never to the
  hostname again;
* HTTPS certificate verification and SNI still use the original hostname;
* redirects are never followed by the transport;
* responses are read with a hard byte ceiling.

Tests can inject a resolver and a connection factory. Production code uses the
real socket and ``http.client`` implementations.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
from dataclasses import dataclass
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

MAX_URL_LENGTH = 2048
DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_MAX_RESPONSE_BYTES = 64 * 1024
DEFAULT_MAX_REQUEST_BYTES = 64 * 1024

_BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "metadata",
    "metadata.google.internal",
    "metadata.google.internal.",
    "instance-data",
    "instance-data.ec2.internal",
    "kubernetes.default",
    "kubernetes.default.svc",
    "host.docker.internal",
    "gateway.docker.internal",
}
_BLOCKED_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".lan",
    ".home",
    ".corp",
    ".intranet",
    ".private",
)


class WebhookSecurityError(Exception):
    """A webhook destination failed a security check.

    ``code`` is safe to store in delivery history. ``message`` is safe to show
    to an administrator and never contains credentials or a resolved address.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class WebhookResponseTooLarge(WebhookSecurityError):
    def __init__(self) -> None:
        super().__init__(
            "response_too_large",
            "The webhook response exceeded the configured byte limit.",
        )


@dataclass(frozen=True)
class ValidatedWebhookTarget:
    url: str
    scheme: str
    host: str
    port: int
    request_path: str
    addresses: tuple[str, ...]

    @property
    def primary_address(self) -> str:
        return self.addresses[0]

    @property
    def is_https(self) -> bool:
        return self.scheme == "https"

    @property
    def host_header(self) -> str:
        host = self.host
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        default = 443 if self.is_https else 80
        return host if self.port == default else f"{host}:{self.port}"


def _normalized_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


def is_forbidden_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True for any address that must never receive a webhook.

    ``is_global`` rejects loopback, private, link-local, multicast, reserved,
    unspecified, carrier-grade NAT, documentation, benchmark and other special
    purpose ranges. Explicit checks keep the intent readable and guard against
    future Python classification changes.
    """

    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return bool(
        not address.is_global
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _parse_url(url: str) -> tuple[str, str, int, str]:
    if not isinstance(url, str):
        raise WebhookSecurityError("invalid_url", "The webhook URL must be text.")
    value = url.strip()
    if not value or len(value) > MAX_URL_LENGTH:
        raise WebhookSecurityError(
            "invalid_url", "Webhook URLs must be 1-2048 characters."
        )
    if any(character.isspace() for character in value):
        raise WebhookSecurityError("invalid_url", "Webhook URLs cannot contain spaces.")
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError:
        raise WebhookSecurityError("invalid_url", "The webhook URL is malformed.") from None
    if parsed.scheme not in {"http", "https"}:
        raise WebhookSecurityError(
            "invalid_scheme", "Webhook URLs must use http:// or https://."
        )
    if parsed.username or parsed.password:
        raise WebhookSecurityError(
            "credentials_in_url", "Webhook URLs cannot contain credentials."
        )
    if not parsed.hostname:
        raise WebhookSecurityError("invalid_host", "Webhook URLs must include a host.")
    if parsed.fragment:
        raise WebhookSecurityError(
            "invalid_url", "Webhook URLs cannot contain a fragment."
        )
    host = parsed.hostname.rstrip(".").lower()
    if not host:
        raise WebhookSecurityError("invalid_host", "Webhook URLs must include a host.")
    if host in _BLOCKED_HOSTNAMES or host.endswith(_BLOCKED_SUFFIXES):
        raise WebhookSecurityError(
            "private_destination",
            "Webhook URLs cannot target local, metadata, or internal hostnames.",
        )
    port = port or (443 if parsed.scheme == "https" else 80)
    if not 1 <= port <= 65535:
        raise WebhookSecurityError("invalid_port", "The webhook port is invalid.")
    request_path = parsed.path or "/"
    if parsed.query:
        request_path = f"{request_path}?{parsed.query}"
    return parsed.scheme, host, port, request_path


def _dedupe(addresses: Iterable[str]) -> tuple[str, ...]:
    seen: list[str] = []
    for address in addresses:
        if address not in seen:
            seen.append(address)
    return tuple(seen)


def resolve_webhook_target(
    url: str,
    *,
    require_https: bool,
    resolver: Callable[..., list[Any]] | None = None,
) -> ValidatedWebhookTarget:
    """Validate a webhook URL and resolve every address it can reach.

    The entire DNS answer set must be globally routable. Rejecting the host when
    *any* answer is private prevents a multi-address DNS response from being
    used to bypass validation during the later connection.
    """

    scheme, host, port, request_path = _parse_url(url)
    if require_https and scheme != "https":
        raise WebhookSecurityError(
            "https_required", "Webhook URLs must use HTTPS in this environment."
        )

    resolve = resolver or socket.getaddrinfo
    addresses: list[str] = []
    try:
        literal = _normalized_address(host)
    except ValueError:
        literal = None

    if literal is not None:
        if is_forbidden_address(literal):
            raise WebhookSecurityError(
                "private_destination",
                "Webhook URLs cannot target private or local network addresses.",
            )
        addresses.append(str(literal))
    else:
        try:
            infos = resolve(host, port, type=socket.SOCK_STREAM)
        except (OSError, UnicodeError, ValueError):
            raise WebhookSecurityError(
                "unresolved_host", "The webhook host could not be resolved."
            ) from None
        for info in infos or []:
            try:
                raw = info[4][0]
                address = _normalized_address(str(raw).split("%", 1)[0])
            except (IndexError, TypeError, ValueError):
                continue
            if is_forbidden_address(address):
                raise WebhookSecurityError(
                    "private_destination",
                    "Webhook URLs cannot target private or local network addresses.",
                )
            addresses.append(str(address))
        addresses = list(_dedupe(addresses))
        if not addresses:
            raise WebhookSecurityError(
                "unresolved_host", "The webhook host did not resolve to a usable address."
            )

    return ValidatedWebhookTarget(
        url=url.strip(),
        scheme=scheme,
        host=host,
        port=port,
        request_path=request_path,
        addresses=tuple(addresses),
    )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to a validated IP while presenting the original hostname.

    ``HTTPSConnection`` normally uses ``self.host`` for both the TCP connection
    and TLS SNI. This subclass separates them: the TCP peer is the validated
    address, while certificate verification and SNI use the real hostname.
    """

    def __init__(
        self,
        ip_address: str,
        port: int,
        *,
        hostname: str,
        timeout: float,
        context: ssl.SSLContext,
    ) -> None:
        super().__init__(ip_address, port, timeout=timeout, context=context)
        self._sni_hostname = hostname

    def connect(self) -> None:
        http.client.HTTPConnection.connect(self)
        self.sock = self._context.wrap_socket(
            self.sock, server_hostname=self._sni_hostname
        )


ConnectionFactory = Callable[[ValidatedWebhookTarget, float, ssl.SSLContext | None], Any]


def _default_connection(
    target: ValidatedWebhookTarget,
    timeout: float,
    context: ssl.SSLContext | None,
) -> Any:
    if target.is_https:
        return _PinnedHTTPSConnection(
            target.primary_address,
            target.port,
            hostname=target.host,
            timeout=timeout,
            context=context or ssl.create_default_context(),
        )
    return http.client.HTTPConnection(
        target.primary_address, target.port, timeout=timeout
    )


def open_pinned_request(
    target: ValidatedWebhookTarget,
    *,
    body: bytes,
    headers: dict[str, str],
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    context: ssl.SSLContext | None = None,
    connection_factory: ConnectionFactory | None = None,
) -> tuple[int, bytes, int]:
    """POST to the validated destination without following redirects.

    Returns ``(status, body_prefix, bytes_read)``. The caller must treat a 3xx
    status as a rejected redirect. The transport never re-resolves the host.
    """

    if len(body) > DEFAULT_MAX_REQUEST_BYTES:
        raise WebhookSecurityError(
            "request_too_large", "The webhook request body exceeds the safe limit."
        )
    if timeout <= 0 or timeout > 30:
        raise WebhookSecurityError("invalid_timeout", "The webhook timeout is invalid.")
    ceiling = max(1, min(int(max_response_bytes), 1024 * 1024))
    factory = connection_factory or _default_connection
    connection = factory(target, float(timeout), context)
    try:
        outgoing = dict(headers)
        outgoing.setdefault("Host", target.host_header)
        connection.request(
            "POST",
            target.request_path,
            body=body,
            headers=outgoing,
        )
        response = connection.getresponse()
        status = int(response.status)
        data = response.read(ceiling + 1)
        if len(data) > ceiling:
            raise WebhookResponseTooLarge()
        return status, data, len(data)
    finally:
        try:
            connection.close()
        except Exception:
            pass


def safe_error(exc: BaseException) -> tuple[str, str]:
    """Map a delivery exception to a safe code/message pair.

    Raw exception text can contain a URL with query-string tokens, an internal
    address, or a TLS certificate path. Only classified messages are returned.
    """

    if isinstance(exc, WebhookResponseTooLarge):
        return exc.code, exc.message
    if isinstance(exc, WebhookSecurityError):
        return exc.code, exc.message
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "timeout", "The webhook endpoint did not respond before the timeout."
    if isinstance(exc, ssl.SSLError):
        return "tls_failure", "The webhook endpoint failed TLS verification."
    if isinstance(exc, (http.client.HTTPException, OSError)):
        return "network_error", "The webhook endpoint could not be reached safely."
    return "delivery_error", "The webhook delivery failed."
