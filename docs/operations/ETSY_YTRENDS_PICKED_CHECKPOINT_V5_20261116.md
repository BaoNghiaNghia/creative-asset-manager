# YTrends Picked checkpointed backfill V5 — 2026-11-16

## Observations

- V4 API + modal released to production at commit `944351856`.
- Production dry-run: 360 current Picked, 495 completed TikTok Listing
  Optimizer drafts (360 latest + 135 historical), 354 unique titles.
- A two-job V4 source-backed pilot updated **2 latest drafts**, raising
  confirmed Etsy keyword rows from **0 to 3** for those two jobs.
- The original full run was interrupted before any writes, because it
  fetched all 354 title sets **before** starting job-level persistence.
  A network outage or runtime interruption could discard all progress.
  Never treat that interrupted invocation as a completed backfill.

## V5 improvements

- Request a geographically neutral cross-title sample (first, median,
  final sorted title), rather than two adjacent alphabetical keywords.
- If all probes lack dated source-backed rows, stop with
  `provider_unavailable_no_writes`, committing nothing.
- For a healthy provider, process windows of `concurrency * 3` titles,
  persisting each window's verified results **before the next network window**.
  Each job uses a short row lock with `SKIP LOCKED`; no raw source title,
  image, credentials or keyword count is invented.
- If two entire consecutive later windows lack dated source-backed rows,
  return `provider_degraded_partial`. Previously committed work is retained;
  the operator must not report that the full catalog is complete.
- Preserve exactly the previous completed description, title, highlights
  and all non-YTrends listing fields; keep newer verified keyword metrics.
- Re-running V5 skips already verified V5 drafts, retaining stable offsets
  based on the complete job list; `--refresh-current` is an intentional
  override if genuinely requesting a fresh source pull.
- `--limit` and `--offset` permit smaller resume batches, with provider
  request concurrency 1–4. Always read `selected_complete_jobs`,
  `selected_jobs`, `titles_processed`, `batches_completed`,
  `updated_latest`, `updated_history`, `verified_rows_before/after`
  and the final status.

## Production execution

Only use the deployed immutable backend release and reviewed environment
loader. No extra AI generation, Etsy crawling, shipping facts or publishing.

```bash
bash scripts/cam-ytrends-refresh-all-picked.sh --tenant creative-assets-prod --limit 2500
bash scripts/cam-ytrends-refresh-all-picked.sh --tenant creative-assets-prod --limit 12 --offset 0 --concurrency 2 --apply
```

Only advance the offset when the previous selected range has been examined.
For the remaining catalog, resume in small stable offsets or run a single
reviewed full scope once provider latency and resource utilization are known.
Do not leave an unobserved long-running process or claim completion until the
dry-run and aggregate verification confirm the actual number updated.
No database migration is needed; release rollback via current/previous is
independent of these source-backed evidence writes.
