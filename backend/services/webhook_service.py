"""Webhooks, notification center and scheduled automation for NexOra OMEGA.

Delivery signing uses a secret derived on demand from the server-side key
pepper, a random per-webhook salt and a rotation version. Only the salt and
version are stored; the signing secret is returned exactly once at creation or
rotation. Enumeration, retries and cleanup are performed by the existing CLI
scheduler path through :class:`WebhookService`.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import time
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from ..db import Database
from ..errors import ApiError, ValidationError
from ..serialization import utcnow

WEBHOOK_EVENTS = (
    "project.created",
    "project.updated",
    "project.expired",
    "project.archived",
    "key.generated",
    "key.activated",
    "key.deactivated",
    "key.expired",
    "key.revoked",
    "key.usage_limit",
    "device.activated",
    "device.deactivated",
    "security.alert",
    "usage.limit_reached",
)

DEFAULT_NOTIFICATION_PREFERENCES = {
    "key_expiring": True,
    "project_expiring": True,
    "failed_deliveries": True,
    "security_alerts": True,
    "usage_limits": True,
    "weekly_summary": False,
}

_SENSITIVE_NAMES = {
    "key",
    "plaintext",
    "key_hash",
    "secret",
    "token",
    "authorization",
    "password",
    "credential",
    "device_token",
}


def sanitize_payload(value: Any, depth: int = 0) -> Any:
    """Recursively drop secret-bearing fields before queuing a webhook payload."""
    if depth > 6:
        return None
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if str(key).lower() in _SENSITIVE_NAMES:
                continue
            cleaned = sanitize_payload(item, depth + 1)
            if cleaned is not None:
                result[str(key)] = cleaned
        return result
    if isinstance(value, list):
        return [
            cleaned
            for item in value[:100]
            if (cleaned := sanitize_payload(item, depth + 1)) is not None
        ]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class WebhookService:
    def __init__(self, db: Database, settings: Any) -> None:
        self.db = db
        self.settings = settings
        self.pepper = getattr(settings, "key_pepper", "")

    # ------------------------------------------------------------------ URLs
    @staticmethod
    def _parse_url(url: str) -> tuple[str, str, int, str]:
        try:
            parsed = urlparse(url)
        except ValueError:
            raise ValidationError({"url": "The webhook URL is malformed."}) from None
        if parsed.scheme not in {"http", "https"}:
            raise ValidationError({"url": "Webhook URLs must use http:// or https://."})
        if not parsed.hostname:
            raise ValidationError({"url": "Webhook URLs must include a host."})
        if parsed.username or parsed.password:
            raise ValidationError({"url": "Webhook URLs cannot contain credentials."})
        if len(url) > 2048:
            raise ValidationError({"url": "Webhook URLs must be 2048 characters or fewer."})
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return parsed.scheme, parsed.hostname, port, parsed.path or "/"

    def _validate_public_url(self, url: str) -> None:
        scheme, host, port, _ = self._parse_url(url)
        if not getattr(self.settings, "is_production", False):
            return
        try:
            literal = ipaddress.ip_address(host)
            addresses = [literal]
        except ValueError:
            try:
                addresses = [
                    ipaddress.ip_address(item[4][0])
                    for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
                ]
            except (OSError, ValueError):
                raise ValidationError({"url": "The webhook host could not be resolved."}) from None
        for address in addresses:
            if (
                address.is_private
                or address.is_loopback
                or address.is_link_local
                or address.is_multicast
                or address.is_reserved
                or address.is_unspecified
            ):
                raise ValidationError(
                    {"url": "Webhook URLs cannot target private or local network addresses."}
                )

    # --------------------------------------------------------------- secrets
    def _secret_for(self, webhook: dict[str, Any]) -> str:
        material = (
            f"nexora:v1:webhook:{webhook['id']}:{webhook['secret_salt']}:"
            f"{webhook['secret_version']}"
        )
        digest = hmac.new(
            self.pepper.encode("utf-8"), material.encode("utf-8"), hashlib.sha256
        ).digest()
        return "whsec_" + digest.hex()

    @staticmethod
    def signature(secret: str, timestamp: int, body: str) -> str:
        return hmac.new(
            secret.encode("utf-8"),
            f"{timestamp}.{body}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    # --------------------------------------------------------------- webhook
    def list_webhooks(self, project_id: str | None = None) -> dict[str, Any]:
        params: tuple[Any, ...] = ()
        clause = ""
        if project_id:
            clause = "WHERE w.project_id = %s"
            params = (project_id,)
        rows = self.db.fetch_all(
            f"""
            SELECT w.id, w.project_id, w.name, w.url, w.events, w.is_active,
                   w.secret_version, w.created_at, w.updated_at,
                   p.project_name,
                   (SELECT COUNT(*)::int FROM nexora_webhook_deliveries d
                     WHERE d.webhook_id = w.id) AS delivery_count,
                   (SELECT COUNT(*)::int FROM nexora_webhook_deliveries d
                     WHERE d.webhook_id = w.id AND d.status = 'delivered') AS delivered_count,
                   (SELECT COUNT(*)::int FROM nexora_webhook_deliveries d
                     WHERE d.webhook_id = w.id AND d.status IN ('failed','dead')) AS failed_count
              FROM nexora_webhooks w
              JOIN nexora_projects p ON p.id = w.project_id
              {clause}
             ORDER BY w.created_at DESC
            """,
            params,
        )
        return {"webhooks": rows, "events": list(WEBHOOK_EVENTS)}

    def create_webhook(
        self,
        *,
        project_id: str,
        name: str,
        url: str,
        events: Any,
        actor: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        project = self.db.fetch_one(
            "SELECT id, project_name FROM nexora_projects WHERE id = %s AND deleted_at IS NULL",
            (project_id,),
        )
        if not project:
            raise ApiError(404, "PROJECT_NOT_FOUND", "Project not found.")
        self._validate_public_url(url)
        name = (name or "").strip()
        if not 1 <= len(name) <= 100:
            raise ValidationError({"name": "Must be 1-100 characters."})
        if not isinstance(events, list) or not events:
            raise ValidationError({"events": "Select at least one event."})
        selected = []
        for event in events:
            if event not in WEBHOOK_EVENTS:
                raise ValidationError({"events": f"Unknown event: {event}"})
            if event not in selected:
                selected.append(event)
        webhook_id = str(uuid4())
        salt = secrets.token_urlsafe(24)
        with self.db.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO nexora_webhooks
                    (id, project_id, name, url, events, secret_salt, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (webhook_id, project_id, name, url, selected, salt, actor.get("id")),
            )
            cursor.execute(
                """
                INSERT INTO admin_audit_logs
                    (admin_user_id, actor_email, action, target_type, target_id,
                     project_id, ip_address, user_agent, request_id, details)
                VALUES (%s, %s, 'webhook.create', 'webhook', %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    actor.get("id"),
                    actor.get("email"),
                    webhook_id,
                    project_id,
                    ip_address,
                    user_agent,
                    request_id,
                    json.dumps({"name": name, "url": url, "events": selected}),
                ),
            )
        row = self.db.fetch_one("SELECT * FROM nexora_webhooks WHERE id = %s", (webhook_id,))
        return {
            "webhook": {key: value for key, value in row.items() if key != "secret_salt"},
            "secret": self._secret_for(row),
            "secret_notice": "Store this signing secret now. It is shown only once.",
        }

    def update_webhook(
        self,
        webhook_id: str,
        *,
        changes: dict[str, Any],
        actor: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        row = self.db.fetch_one("SELECT * FROM nexora_webhooks WHERE id = %s", (webhook_id,))
        if not row:
            raise ApiError(404, "WEBHOOK_NOT_FOUND", "Webhook not found.")
        allowed = {"name", "url", "events", "is_active"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValidationError({"body": f"Unknown fields: {sorted(unknown)}"})
        name = str(changes.get("name", row["name"])).strip()
        url = str(changes.get("url", row["url"])).strip()
        if not 1 <= len(name) <= 100:
            raise ValidationError({"name": "Must be 1-100 characters."})
        self._validate_public_url(url)
        events = changes.get("events", row["events"])
        if isinstance(events, list):
            if not events or any(event not in WEBHOOK_EVENTS for event in events):
                raise ValidationError({"events": "Select at least one known event."})
            events = list(dict.fromkeys(events))
        else:
            raise ValidationError({"events": "Must be an array."})
        active = bool(changes.get("is_active", row["is_active"]))
        with self.db.cursor() as cursor:
            cursor.execute(
                """
                UPDATE nexora_webhooks
                   SET name = %s, url = %s, events = %s, is_active = %s,
                       updated_at = now()
                 WHERE id = %s
                """,
                (name, url, events, active, webhook_id),
            )
            cursor.execute(
                """
                INSERT INTO admin_audit_logs
                    (admin_user_id, actor_email, action, target_type, target_id,
                     project_id, ip_address, user_agent, request_id, details)
                VALUES (%s, %s, 'webhook.update', 'webhook', %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    actor.get("id"),
                    actor.get("email"),
                    webhook_id,
                    str(row["project_id"]),
                    ip_address,
                    user_agent,
                    request_id,
                    json.dumps({"name": name, "url": url, "events": events, "is_active": active}),
                ),
            )
        return self.list_webhooks(str(row["project_id"]))

    def delete_webhook(
        self,
        webhook_id: str,
        *,
        actor: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        row = self.db.fetch_one("SELECT * FROM nexora_webhooks WHERE id = %s", (webhook_id,))
        if not row:
            raise ApiError(404, "WEBHOOK_NOT_FOUND", "Webhook not found.")
        with self.db.cursor() as cursor:
            cursor.execute("DELETE FROM nexora_webhooks WHERE id = %s", (webhook_id,))
            cursor.execute(
                """
                INSERT INTO admin_audit_logs
                    (admin_user_id, actor_email, action, target_type, target_id,
                     project_id, ip_address, user_agent, request_id, details)
                VALUES (%s, %s, 'webhook.delete', 'webhook', %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    actor.get("id"),
                    actor.get("email"),
                    webhook_id,
                    str(row["project_id"]),
                    ip_address,
                    user_agent,
                    request_id,
                    json.dumps({"name": row["name"], "url": row["url"]}),
                ),
            )
        return {"id": webhook_id, "deleted": True}

    def rotate_secret(
        self,
        webhook_id: str,
        *,
        actor: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        row = self.db.fetch_one("SELECT * FROM nexora_webhooks WHERE id = %s", (webhook_id,))
        if not row:
            raise ApiError(404, "WEBHOOK_NOT_FOUND", "Webhook not found.")
        salt = secrets.token_urlsafe(24)
        with self.db.cursor() as cursor:
            cursor.execute(
                """
                UPDATE nexora_webhooks
                   SET secret_salt = %s, secret_version = secret_version + 1,
                       updated_at = now()
                 WHERE id = %s
                 RETURNING secret_version
                """,
                (salt, webhook_id),
            )
            version = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO admin_audit_logs
                    (admin_user_id, actor_email, action, target_type, target_id,
                     project_id, ip_address, user_agent, request_id, details)
                VALUES (%s, %s, 'webhook.secret_rotate', 'webhook', %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    actor.get("id"),
                    actor.get("email"),
                    webhook_id,
                    str(row["project_id"]),
                    ip_address,
                    user_agent,
                    request_id,
                    json.dumps({"secret_version": version}),
                ),
            )
        updated = self.db.fetch_one("SELECT * FROM nexora_webhooks WHERE id = %s", (webhook_id,))
        return {
            "id": webhook_id,
            "secret": self._secret_for(updated),
            "secret_version": version,
            "secret_notice": "The previous secret is no longer valid. Store this now.",
        }

    def list_deliveries(self, webhook_id: str, *, limit: int = 50) -> dict[str, Any]:
        if not self.db.fetch_one("SELECT 1 FROM nexora_webhooks WHERE id = %s", (webhook_id,)):
            raise ApiError(404, "WEBHOOK_NOT_FOUND", "Webhook not found.")
        rows = self.db.fetch_all(
            """
            SELECT id, event_id, event_type, status, attempts, response_code,
                   error, next_attempt_at, created_at, delivered_at
              FROM nexora_webhook_deliveries
             WHERE webhook_id = %s ORDER BY created_at DESC LIMIT %s
            """,
            (webhook_id, max(1, min(200, limit))),
        )
        return {"deliveries": rows}

    def enqueue_event(
        self, event_type: str, project_id: str, payload: dict[str, Any]
    ) -> int:
        if event_type not in WEBHOOK_EVENTS:
            return 0
        clean = sanitize_payload(payload)
        clean["event"] = event_type
        clean["event_id"] = str(uuid4())
        clean["occurred_at"] = utcnow().isoformat()
        event_id = clean["event_id"]
        hooks = self.db.fetch_all(
            """
            SELECT id FROM nexora_webhooks
             WHERE project_id = %s AND is_active = true AND %s = ANY(events)
            """,
            (project_id, event_type),
        )
        queued = 0
        for hook in hooks:
            try:
                self.db.execute(
                    """
                    INSERT INTO nexora_webhook_deliveries
                        (webhook_id, event_id, event_type, payload, status, next_attempt_at)
                    VALUES (%s, %s, %s, %s::jsonb, 'pending', now())
                    ON CONFLICT (webhook_id, event_id) DO NOTHING
                    """,
                    (hook["id"], event_id, event_type, json.dumps(clean)),
                )
                queued += 1
            except Exception:
                continue
        return queued

    def test_webhook(
        self,
        webhook_id: str,
        *,
        actor: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        row = self.db.fetch_one("SELECT * FROM nexora_webhooks WHERE id = %s", (webhook_id,))
        if not row:
            raise ApiError(404, "WEBHOOK_NOT_FOUND", "Webhook not found.")
        event_id = str(uuid4())
        payload = {
            "event": "webhook.test",
            "event_id": event_id,
            "occurred_at": utcnow().isoformat(),
            "webhook_id": webhook_id,
            "message": "NexOra webhook test delivery.",
        }
        self.db.execute(
            """
            INSERT INTO nexora_webhook_deliveries
                (webhook_id, event_id, event_type, payload, status, next_attempt_at)
            VALUES (%s, %s, 'webhook.test', %s::jsonb, 'pending', now())
            """,
            (webhook_id, event_id, json.dumps(payload)),
        )
        return {
            "queued": True,
            "delivery_event_id": event_id,
            "message": "Queued for the automation worker. Check delivery history for the result.",
        }

    # -------------------------------------------------------------- delivery
    def process_deliveries(self, *, limit: int = 20) -> dict[str, int]:
        rows = self.db.fetch_all(
            """
            SELECT d.*, w.url, w.secret_salt, w.secret_version, w.id AS webhook_id
              FROM nexora_webhook_deliveries d
              JOIN nexora_webhooks w ON w.id = d.webhook_id
             WHERE d.status IN ('pending','failed')
               AND w.is_active = true
               AND (d.next_attempt_at IS NULL OR d.next_attempt_at <= now())
               AND d.attempts < 6
             ORDER BY d.created_at ASC LIMIT %s
            """,
            (max(1, min(100, limit)),),
        )
        delivered = failed = dead = 0
        for row in rows:
            attempts = int(row["attempts"]) + 1
            ok = False
            response_code = None
            error = ""
            try:
                self._validate_public_url(row["url"])
                secret = self._secret_for(
                    {
                        "id": row["webhook_id"],
                        "secret_salt": row["secret_salt"],
                        "secret_version": row["secret_version"],
                    }
                )
                body = json.dumps(row["payload"], separators=(",", ":"), default=str)
                timestamp = int(time.time())
                signature = self.signature(secret, timestamp, body)
                request = Request(
                    row["url"],
                    data=body.encode("utf-8"),
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "NexOra-Webhooks/1.0",
                        "X-NexOra-Event": str(row["event_type"]),
                        "X-NexOra-Delivery": str(row["event_id"]),
                        "X-NexOra-Signature": f"t={timestamp},v1={signature}",
                    },
                )
                with urlopen(request, timeout=5) as response:
                    response_code = int(response.status)
                    response.read(4096)
                    ok = 200 <= response_code < 300
                    if not ok:
                        error = f"HTTP {response_code}"
            except HTTPError as exc:
                response_code = int(exc.code)
                error = f"HTTP {exc.code}"
            except (URLError, TimeoutError, OSError) as exc:
                error = str(exc)[:300]
            except ValidationError as exc:
                error = exc.message[:300]
            except Exception as exc:
                error = str(exc)[:300]
            if ok:
                delivered += 1
                self.db.execute(
                    """
                    UPDATE nexora_webhook_deliveries
                       SET status = 'delivered', attempts = %s, response_code = %s,
                           error = '', delivered_at = now()
                     WHERE id = %s
                    """,
                    (attempts, response_code, row["id"]),
                )
            else:
                dead = attempts >= 6
                if dead:
                    status = "dead"
                else:
                    status = "failed"
                delay = min(3600, 60 * (2 ** max(0, attempts - 1)))
                if dead:
                    failed += 0
                else:
                    failed += 1
                self.db.execute(
                    """
                    UPDATE nexora_webhook_deliveries
                       SET status = %s, attempts = %s, response_code = %s,
                           error = %s, next_attempt_at = now() + (%s || ' seconds')::interval
                     WHERE id = %s
                    """,
                    (status, attempts, response_code, error, str(delay), row["id"]),
                )
                if dead:
                    try:
                        self.db.execute(
                            """
                            INSERT INTO nexora_notifications
                                (project_id, type, severity, title, body, metadata)
                            VALUES (%s, 'webhook.dead', 'warning',
                                    'Webhook delivery failed permanently',
                                    'A webhook delivery exhausted its retry budget.',
                                    %s::jsonb)
                            """,
                            (
                                row["project_id"],
                                json.dumps(
                                    {
                                        "webhook_id": str(row["webhook_id"]),
                                        "event_id": str(row["event_id"]),
                                        "error": error[:200],
                                    }
                                ),
                            ),
                        )
                    except Exception:
                        pass
        return {"checked": len(rows), "delivered": delivered, "failed": failed, "dead": dead}

    # ---------------------------------------------------------- notifications
    def list_notifications(
        self, *, limit: int = 50, unread_only: bool = False
    ) -> dict[str, Any]:
        clause = "WHERE read_at IS NULL" if unread_only else ""
        rows = self.db.fetch_all(
            f"""
            SELECT id, project_id, key_id, type, severity, title, body, metadata,
                   read_at, created_at
              FROM nexora_notifications
              {clause}
             ORDER BY created_at DESC LIMIT %s
            """,
            (max(1, min(200, limit)),),
        )
        unread = self.db.fetch_value(
            "SELECT COUNT(*)::int FROM nexora_notifications WHERE read_at IS NULL"
        ) or 0
        return {"notifications": rows, "unread": int(unread)}

    def mark_notification(
        self, notification_id: str, *, read: bool = True
    ) -> dict[str, Any]:
        row = self.db.fetch_one(
            "SELECT id FROM nexora_notifications WHERE id = %s", (notification_id,)
        )
        if not row:
            raise ApiError(404, "NOTIFICATION_NOT_FOUND", "Notification not found.")
        self.db.execute(
            "UPDATE nexora_notifications SET read_at = %s WHERE id = %s",
            (utcnow() if read else None, notification_id),
        )
        return {"id": notification_id, "read": read}

    def mark_all_notifications_read(self) -> dict[str, Any]:
        count = self.db.execute(
            "UPDATE nexora_notifications SET read_at = now() WHERE read_at IS NULL"
        )
        return {"marked": count}

    def get_preferences(self) -> dict[str, Any]:
        row = self.db.fetch_one(
            "SELECT preferences FROM nexora_notification_preferences ORDER BY updated_at DESC LIMIT 1"
        )
        prefs = dict(DEFAULT_NOTIFICATION_PREFERENCES)
        if row and isinstance(row.get("preferences"), dict):
            prefs.update({
                key: value
                for key, value in row["preferences"].items()
                if key in DEFAULT_NOTIFICATION_PREFERENCES and isinstance(value, bool)
            })
        return {"preferences": prefs, "available": sorted(DEFAULT_NOTIFICATION_PREFERENCES)}

    def update_preferences(self, preferences: Any) -> dict[str, Any]:
        if not isinstance(preferences, dict):
            raise ValidationError({"preferences": "Must be an object."})
        unknown = set(preferences) - set(DEFAULT_NOTIFICATION_PREFERENCES)
        if unknown:
            raise ValidationError({"preferences": f"Unknown settings: {sorted(unknown)}"})
        for key, value in preferences.items():
            if not isinstance(value, bool):
                raise ValidationError({"preferences": f"{key} must be true or false."})
        existing = self.get_preferences()["preferences"]
        existing.update(preferences)
        with self.db.cursor() as cursor:
            cursor.execute("DELETE FROM nexora_notification_preferences WHERE admin_user_id IS NULL")
            cursor.execute(
                """
                INSERT INTO nexora_notification_preferences (admin_user_id, preferences)
                VALUES (NULL, %s::jsonb)
                """,
                (json.dumps(existing),),
            )
        return {"preferences": existing}

    def list_automation_runs(self, *, limit: int = 20) -> dict[str, Any]:
        rows = self.db.fetch_all(
            """
            SELECT id, job, status, details, error, started_at, finished_at
              FROM nexora_automation_runs ORDER BY started_at DESC LIMIT %s
            """,
            (max(1, min(100, limit)),),
        )
        return {"runs": rows}

    # ------------------------------------------------------------- automation
    def _setting_int(self, key: str, default: int, minimum: int, maximum: int) -> int:
        try:
            raw = self.db.fetch_value("SELECT value FROM system_settings WHERE key = %s", (key,))
            value = int(str(raw)) if raw is not None else default
            return max(minimum, min(maximum, value))
        except Exception:
            return default

    def run_automation(self) -> dict[str, Any]:
        run_id = self.db.fetch_value(
            "INSERT INTO nexora_automation_runs (job, status) VALUES ('automation.run', 'running') RETURNING id"
        )
        details: dict[str, Any] = {}
        try:
            expired_projects = self.db.execute(
                """
                UPDATE nexora_projects
                   SET status = 'expired', updated_at = now()
                 WHERE status = 'active' AND deleted_at IS NULL
                   AND expires_at IS NOT NULL AND expires_at <= now()
                """
            )
            details["projects_expired"] = expired_projects
            self.db.execute(
                """
                UPDATE reader_keys k
                   SET status = 'expired'
                  FROM nexora_projects p
                 WHERE p.id = k.project_id AND p.status = 'expired'
                   AND k.status IN ('generated','active','deactivated')
                   AND k.revoked_at IS NULL
                """
            )
            expire_keys = self.db.execute(
                """
                UPDATE reader_keys
                   SET status = 'expired'
                 WHERE status IN ('generated','active','deactivated')
                   AND expires_at IS NOT NULL AND expires_at <= now()
                   AND revoked_at IS NULL
                """
            )
            details["keys_expired"] = expire_keys

            retention = self._setting_int(
                "nexora.expired_key_retention_days", 7, 0, 365
            )
            expired_ids = [
                str(row["id"])
                for row in self.db.fetch_all(
                    """
                    SELECT id FROM reader_keys
                     WHERE status = 'expired' AND expires_at IS NOT NULL
                       AND expires_at <= now() - (%s || ' days')::interval
                       AND COALESCE(is_permanent, false) = false
                     ORDER BY expires_at ASC LIMIT 250
                    """,
                    (str(retention),),
                )
            ]
            cleaned = 0
            for key_id in expired_ids:
                with self.db.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM device_tokens WHERE installation_id IN (SELECT id FROM reader_installations WHERE reader_key_id = %s)",
                        (key_id,),
                    )
                    cursor.execute(
                        "DELETE FROM reader_installations WHERE reader_key_id = %s",
                        (key_id,),
                    )
                    cursor.execute(
                        "DELETE FROM nexora_devices WHERE key_id = %s", (key_id,)
                    )
                    cursor.execute(
                        "DELETE FROM reader_keys WHERE id = %s AND status = 'expired'",
                        (key_id,),
                    )
                    cleaned += cursor.rowcount
            details["expired_keys_cleaned"] = cleaned
            details["retention_days"] = retention

            inactive_days = self._setting_int("nexora.inactive_device_days", 30, 0, 3650)
            inactive = 0
            if inactive_days:
                inactive = self.db.execute(
                    """
                    UPDATE nexora_devices
                       SET status = 'deactivated', deactivated_at = now()
                     WHERE status = 'active'
                       AND last_seen_at <= now() - (%s || ' days')::interval
                    """,
                    (str(inactive_days),),
                )
            details["devices_deactivated"] = inactive
            details["inactive_device_days"] = inactive_days

            expiring_keys = self.db.fetch_all(
                """
                SELECT k.id, k.project_id, k.key_hint, k.expires_at, p.project_name
                  FROM reader_keys k JOIN nexora_projects p ON p.id = k.project_id
                 WHERE k.status IN ('generated','active') AND k.expires_at IS NOT NULL
                   AND k.expires_at > now() AND k.expires_at <= now() + interval '7 days'
                 ORDER BY k.expires_at ASC LIMIT 100
                """
            )
            key_notifications = 0
            for item in expiring_keys:
                dedupe = f"key_expiring:{item['id']}:{str(item['expires_at'])[:10]}"
                exists = self.db.fetch_one(
                    "SELECT 1 FROM nexora_notifications WHERE metadata->>'dedupe_key' = %s",
                    (dedupe,),
                )
                if exists:
                    continue
                self.db.execute(
                    """
                    INSERT INTO nexora_notifications
                        (project_id, key_id, type, severity, title, body, metadata)
                    VALUES (%s, %s, 'key.expiring', 'warning',
                            'A key is expiring soon',
                            %s, %s::jsonb)
                    """,
                    (
                        str(item["project_id"]),
                        str(item["id"]),
                        f"Key {item['key_hint']} in {item['project_name']} expires within seven days.",
                        json.dumps({"dedupe_key": dedupe}),
                    ),
                )
                key_notifications += 1
            details["key_expiry_notifications"] = key_notifications

            expiring_projects = self.db.fetch_all(
                """
                SELECT id, project_name, expires_at FROM nexora_projects
                 WHERE status = 'active' AND archived_at IS NULL AND deleted_at IS NULL
                   AND expires_at IS NOT NULL AND expires_at > now()
                   AND expires_at <= now() + interval '7 days'
                 LIMIT 100
                """
            )
            project_notifications = 0
            for item in expiring_projects:
                dedupe = f"project_expiring:{item['id']}:{str(item['expires_at'])[:10]}"
                exists = self.db.fetch_one(
                    "SELECT 1 FROM nexora_notifications WHERE metadata->>'dedupe_key' = %s",
                    (dedupe,),
                )
                if exists:
                    continue
                self.db.execute(
                    """
                    INSERT INTO nexora_notifications
                        (project_id, type, severity, title, body, metadata)
                    VALUES (%s, 'project.expiring', 'warning',
                            'A project is expiring soon', %s, %s::jsonb)
                    """,
                    (
                        str(item["id"]),
                        f"Project {item['project_name']} expires within seven days.",
                        json.dumps({"dedupe_key": dedupe}),
                    ),
                )
                project_notifications += 1
            details["project_expiry_notifications"] = project_notifications

            delivery = self.process_deliveries(limit=50)
            details["webhooks"] = delivery
            self.db.execute(
                """
                UPDATE nexora_automation_runs
                   SET status = 'success', details = %s::jsonb, finished_at = now()
                 WHERE id = %s
                """,
                (json.dumps(details), run_id),
            )
            return {"id": run_id, "status": "success", "details": details}
        except Exception as exc:
            self.db.execute(
                """
                UPDATE nexora_automation_runs
                   SET status = 'failed', error = %s, finished_at = now()
                 WHERE id = %s
                """,
                (str(exc)[:1000], run_id),
            )
            raise
