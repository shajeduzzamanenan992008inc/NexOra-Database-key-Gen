# Webhook Delivery Security

Status: implemented in `backend/services/webhook_security.py` and exercised by
local tests. No live webhook endpoint or production network was contacted.

## Threats addressed

The original design validated a hostname with DNS, then let the HTTP client
resolve it again. A hostile DNS server could answer with a public address during
validation and a private address during delivery (DNS rebinding). Redirects,
oversized responses, and exception text could also leak internal destinations or
turn a webhook into an internal port scanner.

## Destination validation

A webhook URL must:

- be 1–2048 characters, HTTP or HTTPS, with no whitespace, credentials, or
  fragment;
- use HTTPS when the application runs in production;
- not use blocked local/metadata hostnames such as `localhost`, `*.local`,
  `*.internal`, `metadata.google.internal`, `instance-data`, or
  `host.docker.internal`;
- resolve to at least one address, and **every** returned address must be
  globally routable.

IPv4-mapped IPv6 addresses are normalized before classification. The address
check rejects loopback, private, link-local, multicast, reserved, unspecified,
carrier-grade NAT, documentation, benchmark, and other non-global ranges.
Rejecting the host when any DNS answer is private prevents a mixed answer set
from being used during the later connection.

## Pinned connection

After validation, delivery connects to the validated IP address, never to the
hostname again. For HTTPS:

- TLS certificate verification remains enabled with a real SSL context;
- SNI and certificate hostname verification use the original hostname;
- the HTTP `Host` header preserves the original host and non-default port.

No code path disables certificate verification. There is no second DNS lookup
between validation and the TCP connection.

## Redirects

The transport never follows redirects. A `3xx` response is recorded as
`redirect_rejected`; the `Location` target is not fetched. This avoids redirects
to loopback, private, metadata, or unvalidated destinations.

## Request and response bounds

| Limit | Default | Configuration |
|---|---|---|
| Connect/read timeout | 5 s | `WEBHOOK_TIMEOUT_SECONDS` (0.5–30) |
| Request body | 64 KiB | `WEBHOOK_MAX_REQUEST_BYTES` (1 KiB–1 MiB) |
| Response body | 64 KiB | `WEBHOOK_MAX_RESPONSE_BYTES` (1 KiB–1 MiB) |
| URL length | 2048 | fixed |
| Retry ceiling | existing delivery policy | `nexora_webhook_deliveries.attempts` |

Responses are read with a hard ceiling; exceeding it is
`response_too_large`. Delivery history stores classified, bounded errors rather
than raw exception text, so tokens embedded in URLs cannot leak into logs.
Delivery retries use the existing bounded attempt/backoff policy and are run by
the automation engine or the standalone scheduler, never by a browser loop.

## Test coverage

`tests/test_project_transfer_roles_scheduler.py` covers:

- public destination acceptance;
- private IPv4, loopback, link-local, IPv6, and mixed public/private DNS
  rejection;
- DNS changing between resolutions, using injected resolvers;
- redirect rejection without following `Location`;
- timeout and safe error mapping;
- oversized response rejection;
- successful pinned delivery with mocked connections;
- webhook URL query-string redaction in project exports.

Tests use injected resolvers and connection factories. Security checks are not
disabled or weakened for tests, and no external network is required.

## Residual risks and operations

- Pinning protects the connection from a second DNS answer, but an egress
  firewall or network policy is still recommended as defense in depth.
- HTTP webhooks are permitted only outside production. Production requires
  HTTPS (`settings.is_production`).
- Delivery workers must run with the same TLS trust store as the application.
- Rotate a webhook signing secret if a destination is suspected compromised;
  project export never includes the signing secret.
- Monitor `nexora_webhook_deliveries` for permanent failures and repeated
  `private_destination`, `redirect_rejected`, or `tls_failure` codes.
