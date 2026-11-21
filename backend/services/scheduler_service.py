"""Standalone, lock-protected automation scheduler.

The Flask web process never runs an infinite loop. A separate worker process
invokes :class:`SchedulerService` through the CLI, cron, systemd, or Windows
Task Scheduler.

Concurrency and idempotency:

* a PostgreSQL session advisory lock guarantees one sweep at a time across
  workers and hosts;
* every job is a bounded, idempotent UPDATE/DELETE or a bounded batch;
* webhook retries use the existing attempt ceiling and backoff;
* each sweep is recorded once in ``nexora_automation_runs``;
* a failed sweep is recorded with a safe error string and does not poison the
  next run.

The service never logs secrets. Errors are classified and bounded.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Iterable

from ..db import Database
from ..serialization import utcnow
from .project_service import ProjectService
from .webhook_service import WebhookService

LOG = logging.getLogger("nexora.scheduler")

# Stable 64-bit advisory lock key. "NEXP" in ASCII.
SCHEDULER_LOCK_KEY = 0x4E455850
JOBS = ("automation", "project_purge", "sessions")


class SchedulerService:
    def __init__(self, db: Database, settings: Any) -> None:
        self.db = db
        self.settings = settings
        self.webhooks = WebhookService(db, settings)
        self.projects = ProjectService(db, settings)

    # ------------------------------------------------------------------ utils
    def _batch_size(self, value: int | None = None) -> int:
        configured = int(getattr(self.settings, "scheduler_batch_size", 100) or 100)
        if value is None:
            return max(1, min(1000, configured))
        return max(1, min(1000, int(value)))

    @staticmethod
    def _selected(jobs: Iterable[str] | None) -> tuple[str, ...]:
        if not jobs:
            return JOBS
        selected = tuple(job for job in JOBS if job in set(jobs))
        if not selected:
            raise ValueError(f"Unknown scheduler jobs; use: {', '.join(JOBS)}")
        return selected

    def _record(
        self,
        *,
        status: str,
        details: dict[str, Any],
        error: str = "",
        duration_ms: int = 0,
        dry_run: bool = False,
        worker_id: str = "",
    ) -> int | None:
        try:
            return int(
                self.db.fetch_value(
                    """
                    INSERT INTO nexora_automation_runs
                        (job, status, details, error, worker_id, duration_ms,
                         dry_run, finished_at)
                    VALUES ('universal.scheduler', %s, %s::jsonb, %s, %s, %s, %s,
                            CASE WHEN %s = 'running' THEN NULL ELSE now() END)
                    RETURNING id
                    """,
                    (
                        status,
                        json.dumps(details, default=str),
                        error[:1000],
                        worker_id,
                        duration_ms,
                        dry_run,
                        status,
                    ),
                )
                or 0
            ) or None
        except Exception:
            LOG.exception("could not record scheduler run")
            return None

    def _audit(
        self,
        *,
        outcome: str,
        details: dict[str, Any],
    ) -> None:
        try:
            self.db.execute(
                """
                INSERT INTO admin_audit_logs
                    (admin_user_id, actor_email, action, target_type, target_id,
                     outcome, details)
                VALUES (NULL, 'scheduler', 'scheduler.run', 'system', NULL,
                        %s, %s::jsonb)
                """,
                (outcome, json.dumps(details, default=str)),
            )
        except Exception:
            LOG.warning("scheduler audit event could not be written")

    def _prune_sessions(self, *, dry_run: bool) -> dict[str, int]:
        if dry_run:
            count = int(
                self.db.fetch_value(
                    """
                    SELECT COUNT(*)::int FROM admin_sessions
                     WHERE expires_at < now() - interval '1 day'
                        OR revoked_at < now() - interval '1 day'
                    """
                )
                or 0
            )
            return {"sessions_pruned": count}
        pruned = self.db.execute(
            """
            DELETE FROM admin_sessions
             WHERE expires_at < now() - interval '1 day'
                OR revoked_at < now() - interval '1 day'
            """
        )
        return {"sessions_pruned": int(pruned or 0)}

    def _purge(self, *, dry_run: bool, limit: int) -> dict[str, int]:
        if dry_run:
            due = int(
                self.db.fetch_value(
                    """
                    SELECT COUNT(*)::int FROM nexora_projects
                     WHERE deleted_at IS NULL AND auto_delete_at IS NOT NULL
                       AND auto_delete_at <= now()
                    """
                )
                or 0
            )
            return {"projects_due": due, "projects_deleted": 0}
        result = self.projects.purge_due_projects(limit=limit)
        return {
            "projects_deleted": int(result.get("deleted_count") or 0),
            "projects_due": 0,
        }

    # ------------------------------------------------------------------- run
    def run_once(
        self,
        *,
        jobs: Iterable[str] | None = None,
        dry_run: bool = False,
        worker_id: str | None = None,
        batch_size: int | None = None,
    ) -> dict[str, Any]:
        """Run one bounded sweep under a cross-process advisory lock."""
        selected = self._selected(jobs)
        worker = (worker_id or os.getenv("SCHEDULER_WORKER_ID") or "cli")[:100]
        limit = self._batch_size(batch_size)
        started = time.perf_counter()
        with self.db.session_lock(SCHEDULER_LOCK_KEY) as acquired:
            if not acquired:
                return {
                    "status": "skipped",
                    "reason": "another scheduler worker holds the lock",
                    "jobs": list(selected),
                    "duration_ms": 0,
                }
            details: dict[str, Any] = {"jobs": list(selected), "dry_run": dry_run}
            try:
                if "automation" in selected:
                    details["automation"] = self.webhooks.run_automation(
                        dry_run=dry_run,
                        record_run=False,
                        worker_id=worker,
                        job="universal.scheduler.automation",
                    )
                if "project_purge" in selected:
                    details["project_purge"] = self._purge(
                        dry_run=dry_run, limit=limit
                    )
                if "sessions" in selected:
                    details["sessions"] = self._prune_sessions(dry_run=dry_run)
                duration_ms = int((time.perf_counter() - started) * 1000)
                run_id = self._record(
                    status="success",
                    details=details,
                    duration_ms=duration_ms,
                    dry_run=dry_run,
                    worker_id=worker,
                )
                self._audit(
                    outcome="success",
                    details={
                        "run_id": run_id,
                        "jobs": list(selected),
                        "dry_run": dry_run,
                        "duration_ms": duration_ms,
                    },
                )
                LOG.info(
                    "scheduler sweep complete jobs=%s dry_run=%s duration_ms=%s",
                    ",".join(selected),
                    dry_run,
                    duration_ms,
                )
                return {
                    "status": "success",
                    "run_id": run_id,
                    "worker_id": worker,
                    "jobs": list(selected),
                    "dry_run": dry_run,
                    "duration_ms": duration_ms,
                    "details": details,
                }
            except Exception as exc:
                duration_ms = int((time.perf_counter() - started) * 1000)
                safe_error = f"{type(exc).__name__}: {str(exc)[:300]}"
                run_id = self._record(
                    status="failed",
                    details=details,
                    error=safe_error,
                    duration_ms=duration_ms,
                    dry_run=dry_run,
                    worker_id=worker,
                )
                self._audit(
                    outcome="failure",
                    details={
                        "run_id": run_id,
                        "jobs": list(selected),
                        "error": safe_error,
                    },
                )
                LOG.exception("scheduler sweep failed")
                return {
                    "status": "failed",
                    "run_id": run_id,
                    "worker_id": worker,
                    "jobs": list(selected),
                    "dry_run": dry_run,
                    "duration_ms": duration_ms,
                    "error": safe_error,
                }

    def run_forever(
        self,
        *,
        interval_seconds: int = 60,
        jobs: Iterable[str] | None = None,
        max_iterations: int | None = None,
        dry_run: bool = False,
        stop_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        """Run sweeps until stopped, bounded by an optional iteration limit."""
        interval = max(5, min(86400, int(interval_seconds)))
        stop = stop_event or threading.Event()
        iterations = 0
        failures = 0
        last: dict[str, Any] = {}
        LOG.info("scheduler worker started interval_seconds=%s", interval)
        try:
            while not stop.is_set():
                if max_iterations is not None and iterations >= max(1, max_iterations):
                    break
                iterations += 1
                last = self.run_once(jobs=jobs, dry_run=dry_run)
                if last.get("status") == "failed":
                    failures += 1
                if max_iterations is not None and iterations >= max(1, max_iterations):
                    break
                if stop.wait(interval):
                    break
        except KeyboardInterrupt:
            LOG.info("scheduler interrupted; shutting down gracefully")
            stop.set()
        finally:
            LOG.info(
                "scheduler worker stopped iterations=%s failures=%s",
                iterations,
                failures,
            )
        return {
            "status": "stopped",
            "iterations": iterations,
            "failures": failures,
            "last": last,
        }

    # ---------------------------------------------------------------- status
    @staticmethod
    def _run_metrics(run: dict[str, Any]) -> dict[str, int]:
        """Extract bounded, non-secret processed/failed counters from run details."""
        details = run.get("details") or {}
        if not isinstance(details, dict):
            return {"processed": 0, "failed": 0}
        automation = details.get("automation")
        project_purge = details.get("project_purge") or {}
        sessions = details.get("sessions") or {}
        processed = 0
        failed = 0
        if isinstance(automation, dict):
            for key in (
                "projects_expired",
                "keys_expired",
                "expired_keys_cleaned",
                "devices_deactivated",
                "key_expiry_notifications",
                "project_expiry_notifications",
            ):
                processed += int(automation.get(key) or 0)
            webhooks = automation.get("webhooks") or {}
            if isinstance(webhooks, dict):
                processed += sum(
                    int(webhooks.get(key) or 0)
                    for key in ("delivered", "failed", "retried")
                )
                failed += int(webhooks.get("failed") or 0)
        processed += int(project_purge.get("projects_deleted") or 0)
        processed += int(sessions.get("sessions_pruned") or 0)
        return {"processed": processed, "failed": failed}

    def status(self) -> dict[str, Any]:
        runs = self.db.fetch_all(
            """
            SELECT id, job, status, details, error, worker_id, duration_ms,
                   dry_run, started_at, finished_at
              FROM nexora_automation_runs
             ORDER BY started_at DESC LIMIT 50
            """
        )
        counts = self.db.fetch_one(
            """
            SELECT
              (SELECT COUNT(*)::int FROM nexora_projects WHERE deleted_at IS NULL) AS projects,
              (SELECT COUNT(*)::int FROM reader_keys) AS keys,
              (SELECT COUNT(*)::int FROM nexora_webhook_deliveries
                WHERE status IN ('pending','failed')) AS webhook_queue,
              (SELECT COUNT(*)::int FROM nexora_projects
                WHERE deleted_at IS NULL AND auto_delete_at IS NOT NULL
                  AND auto_delete_at <= now()) AS projects_due,
              (SELECT COUNT(*)::int FROM nexora_notifications
                WHERE read_at IS NULL) AS unread_notifications,
              (SELECT COUNT(*)::int FROM nexora_webhook_deliveries
                WHERE status = 'dead') AS webhook_dead
            """
        ) or {}
        interval = int(getattr(self.settings, "scheduler_interval_seconds", 60) or 60)
        last_success = next(
            (row for row in runs if row.get("status") == "success"), None
        )
        last_failure = next(
            (row for row in runs if row.get("status") == "failed"), None
        )
        last_run = runs[0] if runs else None
        lock_held = int(
            self.db.fetch_value(
                """
                SELECT COUNT(*)::int FROM pg_locks
                 WHERE locktype = 'advisory' AND granted = true
                   AND classid = 0 AND objid = %s
                """,
                (SCHEDULER_LOCK_KEY,),
            )
            or 0
        ) > 0
        if not last_run:
            worker_status = "unknown"
        elif last_run.get("finished_at") is None:
            worker_status = "running"
        else:
            finished = last_run.get("finished_at")
            age = time.time() - finished.timestamp() if finished else None
            worker_status = (
                "stale"
                if age is not None and age > max(120, interval * 3)
                else "idle"
            )
        per_job: list[dict[str, Any]] = []
        seen_jobs: set[str] = set()
        for run in runs:
            job = str(run.get("job") or "")
            if not job or job in seen_jobs:
                continue
            seen_jobs.add(job)
            metrics = self._run_metrics(run)
            per_job.append(
                {
                    "job": job,
                    "status": run.get("status"),
                    "last_run": run.get("started_at"),
                    "last_finished": run.get("finished_at"),
                    "duration_ms": int(run.get("duration_ms") or 0),
                    "processed": metrics["processed"],
                    "failed": metrics["failed"],
                    "worker_id": run.get("worker_id") or "",
                    "dry_run": bool(run.get("dry_run")),
                    "error": run.get("error") or "",
                }
            )
        return {
            "jobs": list(JOBS),
            "lock_key": SCHEDULER_LOCK_KEY,
            "lock_held": lock_held,
            "worker_status": worker_status,
            "interval_seconds": interval,
            "batch_size": self._batch_size(),
            "counts": {key: int(value or 0) for key, value in counts.items()},
            "last_success": last_success.get("started_at") if last_success else None,
            "last_failure": last_failure.get("started_at") if last_failure else None,
            "last_failure_reason": (last_failure.get("error") or "")
            if last_failure
            else "",
            "last_run": last_run.get("started_at") if last_run else None,
            "worker_id": last_run.get("worker_id") if last_run else "",
            "per_job": per_job,
            "runs": runs[:10],
        }
