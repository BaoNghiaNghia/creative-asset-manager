# Etsy Spy: latency, 502 and thumbnail cache runbook

This release adds **bounded five-minute metrics** for fixed Etsy Spy GET routes.
No tenant ID, query string, listing key, Design URL, SHA or token is logged.
Only HTTP status, a route class, latency buckets and cache counters enter
`app.etsy_spy_performance` logs. One log line per route per completed
five-minute window keeps overhead and log growth bounded.

## Read-only 24-hour report

Run on the CAM server (no secrets or API keys):

```bash
journalctl -u creative-asset-manager-api --since '24 hours ago' --no-pager -o cat |
  python3 scripts/cam-etsy-perf-report.py --hours 24
```

The summary shows per-route request counts, API 5xx, API 502, p95 **upper bound**
from exact accumulated histogram buckets, thumbnail RAM-cache hit/miss counts,
and coalesced Drive reads. A current window is emitted once traffic reaches
the next five-minute window; summaries may therefore lag by up to five minutes.
Metrics are process-local during each window, then persisted in journal output.
A server restart can lose part of the currently incomplete window.

**Important limitation:** Nginx-generated 502/504 responses do not reach the
API middleware and cannot be counted as API 502. Correlate these with read-only
Nginx access/error logs or approved reverse-proxy monitoring. No changes to
Nginx cache policy are made, and authenticated Design images must never enter
a shared proxy/CDN cache.

## Response safety

- A browser retries a failed GET once after a small delay (or bounded
  Retry-After), never POST/PATCH/PUT/DELETE actions.
- A browser abort cancels the pending retry; auth failures are never retried.
- Concurrent requests for the same tenant, remote thumbnail SHA and size
  share one in-flight Drive download. Unique requests still operate normally.
- OAuth/ownership validation runs for every request, and the existing
  tenant/Picked ACL executes **before** any cache lookup in the router.
- Native Master originals are not cached or shared; failed or checksum-mismatch
  responses are not added to the thumbnail cache.

## Follow-up diagnosis

If API p95 is high on `summary` or `listings`, inspect database execution
plans for those precise endpoints with safe, read-only access; do not add an
unbounded query result cache or relax tenant-scoped authorization.
If the edge returns 502 but API 5xx and p95 are normal, inspect Nginx upstream
timeouts, connection resets and host memory/swap pressure before changing
application code. Preserve current and previous immutable rollback releases.
