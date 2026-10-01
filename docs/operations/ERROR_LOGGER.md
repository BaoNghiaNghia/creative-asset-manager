# System Error Logger

Creative Asset Manager archives operational errors from all systemd units matching
`creative-asset-manager-*`. The archive exists independently of journald retention
and keeps at most the most recent 10 days.

## What is captured

The collector scans journald every five minutes and archives:

- structured `ERROR`, `CRITICAL`, or `FATAL` log records;
- structured records carrying an exception;
- non-success `error_code` values, even when the source logger emitted them at
  INFO level;
- explicit failed/error terminal statuses;
- deferred/retry operational conditions, classified separately as `warning`;
- plain Python/uvicorn/systemd messages containing errors, exceptions,
  tracebacks, fatal failures, OOMs, or process failures.

Normal INFO traffic and harmless library WARNING diagnostics are ignored. The
`stats` command groups the archive by severity, service, and top recurring
error pattern so high-volume retry/defer conditions do not hide hard failures.

Before an entry is written, URL query strings/fragments and common secret fields
such as bearer tokens, cookies, API keys, refresh/access tokens, client secrets,
and passwords are redacted. Individual archived messages are capped at 40,000
characters.

## Storage and retention

Production state is stored under:

`/var/lib/creative-asset-manager/error-logger/`

Daily JSONL archives live in the `archive/` directory. The collector stores the
last journald cursor in `journal.cursor`, so normal runs only scan new entries.
The first run backfills the preceding 10 days. Archive files older than the
10-day retention window are removed automatically.

## Operations

Collect immediately:

```bash
sudo /opt/creative-asset-manager/current/scripts/cam-error-logger.py collect
```

Show recent errors:

```bash
sudo /opt/creative-asset-manager/current/scripts/cam-error-logger.py show --days 10 --limit 100
```

Filter by service or text:

```bash
sudo /opt/creative-asset-manager/current/scripts/cam-error-logger.py show --service visual-worker --search timeout
```

Get counts by service/severity:

```bash
sudo /opt/creative-asset-manager/current/scripts/cam-error-logger.py stats --days 10
```

Timer status:

```bash
systemctl status creative-asset-manager-error-logger.timer
journalctl -u creative-asset-manager-error-logger.service --since today
```

The archive is diagnostic data. It must not replace service health checks,
database job state, or application audit records.
