# Automation Scheduler

Status: implemented and tested against the local PostgreSQL test database.
The application starts the worker automatically; no production schedule is
created by this repository.

## Architecture

The application process starts one embedded daemon worker after the Flask app
is built. It is the same `SchedulerService` used by the CLI, not a second
scheduler. Deployments that prefer an external process set
`SCHEDULER_EMBEDDED=0` and run the CLI from cron, systemd, or Windows Task
Scheduler. The database advisory lock makes either mode safe, including
overlapping web workers.

Each sweep:

1. acquires the PostgreSQL session advisory lock `0x4E455850` (`NEXP`);
2. runs the selected bounded jobs;
3. records one row in `nexora_automation_runs` with status, safe details,
   duration, worker id, and dry-run flag;
4. writes a safe `scheduler.run` audit event;
5. releases the lock.

If another worker holds the lock, the sweep returns `status: "skipped"` instead
of running duplicate work. Jobs are idempotent UPDATE/DELETE operations or
bounded batches. Webhook retries keep the existing attempt ceiling and backoff.
A failed sweep is recorded and the loop continues on the next interval.

## Expiration lifecycle

`expires_at` is the canonical instant for both expiry and automatic deletion:

* `reader_keys.expires_at` — the key is rejected at that instant and then
  hard-deleted according to `nexora.expired_key_retention_days` (default `0`,
  meaning at expiry). `never`/permanent keys with no explicit deletion
  timestamp are never deleted.
* `nexora_projects.expires_at = nexora_projects.auto_delete_at` — the project
  and its keys, installations, device tokens and devices are deleted at that
  instant. A `never_expires = true` project with no `auto_delete_at` is never
  deleted; an explicit `auto_delete_at` is honored as its deletion schedule.

Enforcement does not wait for a sweep: authentication, activation and key
generation check the timestamps first. The sweep only removes records that are
already unusable. Each deletion is a separate transaction with a strict audit
row (`reader_key.auto_delete` or `project.delete`); a failed deletion rolls
back only its own transaction and is retried next sweep. Plaintext keys are
never logged.

## Jobs

| Job | Work |
|---|---|
| `automation` | The established universal automation sweep: project/key expiration, expired-key deletion, project purge, notification processing, and webhook delivery retries. |
| `project_purge` | Permanently deletes due projects through `ProjectService.purge_due_projects`, including related records. |
| `key_purge` | Permanently deletes due keys through `ProjectService.purge_expired_keys`, including installations, device tokens and device bindings. |
| `sessions` | Removes administrator sessions that expired or were revoked more than one day ago. |

All jobs run by default. Select a subset with `--jobs`. The `automation` job
also performs both purges, so the dedicated purge jobs are idempotent
supplements for external schedulers that want a smaller scope.

## Commands

Use the repository's real CLI:

```bash
python -m backend.cli universal-scheduler-run --once
python -m backend.cli universal-scheduler-run --once --dry-run
python -m backend.cli universal-scheduler-run --once --jobs automation,project_purge
python -m backend.cli universal-scheduler-run --interval 60
python -m backend.cli universal-scheduler-status
python -m backend.cli universal-automation-run
python -m backend.cli universal-automation-run --dry-run
```

`universal-scheduler-run` loops only in the worker process. `--once` is the
recommended cron mode. `--max-iterations` bounds loop mode for testing.
`Ctrl+C` stops the loop gracefully.

Exit codes:

| Code | Meaning |
|---|---|
| `0` | Sweep/status succeeded, or the lock was already held. |
| `1` | Automation or sweep failed. |
| `2` | Configuration or command-line error. |
| `3` | Database unavailable. |

`universal-automation-run` runs only the automation job and records its own run.
The scheduler calls the same engine with `record_run=False` so one sweep produces
one ledger row.

## Configuration

| Variable | Default | Range / meaning |
|---|---|---|
| `SCHEDULER_EMBEDDED` | `1` | Start the in-process worker. Set `0` when a dedicated cron/systemd worker is deployed. |
| `SCHEDULER_BATCH_SIZE` | `100` | 1–1000 records per purge batch. |
| `SCHEDULER_INTERVAL_SECONDS` | `60` | 5–86400; default interval reported by status. |
| `SCHEDULER_WORKER_ID` | `cli` | Safe worker label stored with the run. |
| `WEBHOOK_TIMEOUT_SECONDS` | `5.0` | 0.5–30 per delivery attempt. |
| `WEBHOOK_MAX_RESPONSE_BYTES` | `65536` | 1 KiB–1 MiB response ceiling. |
| `WEBHOOK_MAX_REQUEST_BYTES` | `65536` | 1 KiB–1 MiB request ceiling. |

The CLI `--interval` flag controls loop mode. Set the environment variable in the
worker's service definition so status and operations agree.

## External worker (optional)

Set `SCHEDULER_EMBEDDED=0` in the web service first, then install one of the
following. Running both is safe (the advisory lock serializes them) but the
external worker is redundant while the embedded worker is enabled.

### Cron

```cron
* * * * * cd /srv/nexora && /srv/nexora/.venv/bin/python -m backend.cli universal-scheduler-run --once >> /var/log/nexora-scheduler.log 2>&1
```

Use a lock-aware process supervisor and one cron entry per environment. The
database advisory lock is the final duplicate-execution guard, not a substitute
for sane scheduling.

## systemd

```ini
[Unit]
Description=NexOra automation scheduler
After=network-online.target postgresql.service

[Service]
Type=simple
WorkingDirectory=/srv/nexora
EnvironmentFile=/srv/nexora/.env
ExecStart=/srv/nexora/.venv/bin/python -m backend.cli universal-scheduler-run --once
```

For a long-running loop, replace `--once` with `--interval 60`, use
`Restart=on-failure`, and stop with `SIGTERM`/`SIGINT`.

## Windows Task Scheduler

Create a task that runs:

```text
D:\FTP\NexOra-Database-key-Gen\.venv\Scripts\python.exe -m backend.cli universal-scheduler-run --once
```

Set the project directory as the start-in folder, load the same environment as
the web service, and enable "Run whether user is logged on or not". Do not run a
second loop worker on the same database unless the advisory lock is intended to
serialize them.

## Monitoring and failure recovery

- `universal-scheduler-status` returns jobs, lock key, interval, batch size,
  due-project counts, webhook queue depth, unread notifications, recent runs,
  and the last successful run.
- `GET /api/v1/universal/automation/scheduler` exposes the same status to an
  administrator with `automation:read`.
- Inspect `nexora_automation_runs` for `status = 'failed'`, bounded `error` text,
  and `duration_ms`.
- Inspect `admin_audit_logs` for `scheduler.run`.
- A failed sweep is recorded and does not block the next run. Fix the reported
  cause, run once with `--dry-run`, then run normally.
- If runs are repeatedly `skipped`, another worker holds the advisory lock.
  Check for a stuck or duplicated worker before restarting anything.
- The scheduler logs counts and durations, never credentials or webhook URLs.

## Production checklist

1. Apply migrations through the approved migration process.
2. Leave `SCHEDULER_EMBEDDED=1` (default) unless an external worker is
   deliberately deployed; in that case set it to `0` in the web service.
3. Confirm startup logs contain `embedded scheduler worker started`, then run
   `universal-scheduler-status` and one `--dry-run` sweep.
4. Confirm at least one successful run and that `last_success` advances.
5. Alert on failed runs, missing recent success, and a growing webhook queue.
6. Configure `nexora.expired_key_retention_days` only if expired keys must be
   kept for a support window; `0` deletes them at expiry.
