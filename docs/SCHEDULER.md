# Standalone Automation Scheduler

Status: implemented and tested against the local PostgreSQL test database.
Nothing was deployed and no production schedule was created.

## Architecture

The Flask web process never runs an infinite automation loop. A separate worker
process invokes `SchedulerService` through the CLI, cron, systemd, or Windows
Task Scheduler.

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

## Jobs

| Job | Work |
|---|---|
| `automation` | The established universal automation sweep: expirations, retention, notification processing, and webhook delivery retries. |
| `project_purge` | Permanently deletes projects whose `auto_delete_at` is due, using the existing `ProjectService.purge_due_projects` batch. |
| `sessions` | Removes administrator sessions that expired or were revoked more than one day ago. |

All jobs run by default. Select a subset with `--jobs`.

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
| `SCHEDULER_BATCH_SIZE` | `100` | 1–1000 records per purge batch. |
| `SCHEDULER_INTERVAL_SECONDS` | `60` | 5–86400; default interval reported by status. |
| `SCHEDULER_WORKER_ID` | `cli` | Safe worker label stored with the run. |
| `WEBHOOK_TIMEOUT_SECONDS` | `5.0` | 0.5–30 per delivery attempt. |
| `WEBHOOK_MAX_RESPONSE_BYTES` | `65536` | 1 KiB–1 MiB response ceiling. |
| `WEBHOOK_MAX_REQUEST_BYTES` | `65536` | 1 KiB–1 MiB request ceiling. |

The CLI `--interval` flag controls loop mode. Set the environment variable in the
worker's service definition so status and operations agree.

## Cron

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

1. Apply migration 010 through the approved migration process.
2. Configure the worker environment with the same `DATABASE_URL` and secret
   settings as the web service, using a secret manager.
3. Run `universal-scheduler-status` and one `--dry-run` sweep.
4. Install exactly one schedule/supervisor per environment.
5. Alert on failed runs, missing recent success, and a growing webhook queue.
