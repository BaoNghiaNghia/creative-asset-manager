# Etsy Picked — YTrends source-safe refresh V4 (2026-11-16)

## Incident and root cause

The TikTok Shop US Listing Optimizer displayed five candidate keyword phrases
with `0/5 từ khóa có số liệu` after **Cập nhật YTrends**. The five phrases are
generated query variants, **not** five independently confirmed Etsy data rows.
The old API only rejected an unavailable refresh when the result had no keyword
array. When the MCP failed but supplied a five-row array of `unavailable`
placeholders, the handler could persist the empty table with HTTP 200.
There is no evidence that any of these missing numbers is a genuine zero.
YTuong publicly acknowledges that its Etsy API can intermittently slow or stop (https://heyetsy.com/tools).

## V4 corrections

- The API accepts a source-backed Etsy response only if at least one keyword
  has a dated, numeric verified listing count OR a dated `no_data` snapshot.
  A five-row `unavailable` array now returns HTTP 503 and does **not** write
  the saved draft.
- Do not replace an older verified table with fewer verified keyword rows.
  Existing source-backed metrics keep their original keyword/date and never
  become numbers for another phrase.
- The UI distinguishes unsourced keyword candidates from actual Etsy data.
  It does not claim TikTok volume, write listings or regenerate descriptions.
- Batch backfill covers **all completed TikTok draft jobs** belonging to picked
  listings, including history. It deduplicates identical titles, batches MCP
  requests, and preserves older verified metrics, descriptions and highlights.
- The first two distinct source titles act as an MCP availability preflight.
  If neither yields a source-backed snapshot, `--apply` stops with
  `provider_unavailable_no_writes`. Do not bypass this control or invent counts.
- `--offset` allows deterministic paging beyond 2500 completed jobs. Execute
  batches until `offset >= eligible_jobs`. Print only aggregate counts, not
  raw source titles, provider responses, tokens or personal metadata.

## Production execution

After reviewed CI, merge to `main`, deploy the matching immutable backend and
frontend releases with the existing authorized scripts, verify
`/live`, `/ready` and frontend build provenance.

Dry run, using the active release (not a development database):

```bash
bash scripts/cam-ytrends-refresh-all-picked.sh --tenant creative-assets-prod --limit 2500
```

Only if provider probes succeed and data backup/rollback state is verified:

```bash
bash scripts/cam-ytrends-refresh-all-picked.sh --tenant creative-assets-prod --limit 2500 --offset 0 --concurrency 2 --apply
```

Report `picked_total`, `eligible_jobs`, `selected_jobs`,
`updated_latest`, `updated_history`, `provider_* `,
`preserved_better_evidence` and `verified_rows_before/after`.
Do not requeue Codex jobs, Gemini, ImageGen or publishing actions.
If the provider is down, stop and retain existing source-backed numbers;
retry the same reviewed script only when an authorized operator resumes.
Rollback the application release via current/previous; no schema change.
