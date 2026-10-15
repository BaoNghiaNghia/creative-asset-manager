# Inventory production rollout

## Safe defaults

All committed Inventory flags are off. Inventory is not activated by starting the Creative worker. The production topology has three independently restartable processes: worker (Creative), inventory-worker, and inventory-scheduler. Inventory uses inventory_jobs, Inventory health on port 8082, and independent tenant processing/AI controls.

## Controlled pilot

1. Upgrade the database to Alembic head.
2. Set INVENTORY_TENANT_ALLOWLIST to one pilot tenant ID in the deployment secret store; never commit tenant IDs, Drive IDs, OAuth tokens, or API keys.
3. Set INVENTORY_AUTOMATION_ENABLED=true and INVENTORY_WORKER_ENABLED=true. Enable Drive polling and the scheduler only after the binding, processing control, and pilot checks are verified.
4. For Inventory AI, set INVENTORY_AI_ENABLED=true only after validating the same-tenant Inventory credential or the explicitly supported Image/Video credential fallback. A dedicated Inventory key is preferred; shared keys consume their original Gemini project quota.
5. Start the inventory compose profile and verify worker live/ready endpoints, Inventory job metrics, and tenant-scoped logs.

## Shared Gemini Image/Video fallback

Inventory can borrow active Gemini credentials from the same tenant's Image and Video pools when its selected Inventory key is unavailable or returns HTTP 401/403. Controlled by INVENTORY_GEMINI_SHARED_FALLBACK_ENABLED (default true; set false to disable). Inventory credentials remain first priority. No keys or ciphertext are copied into Inventory storage; decryption stays within the original credential repository.

Each Inventory request is limited to six credential attempts. HTTP 429, transport errors, model-not-found, and bad provider responses never cause further Image/Video quota borrowing. Image credentials are considered before Video. Provider project quota does not increase by sharing a key. The gateway logs safe fallback metadata without secrets. A ListModels key probe can establish key acceptance but not permissions for a particular generateContent model.

Do not automatically replay an old Morning Reset into an active workbook outside the daily safe window. Check verified closing/opening state and use the explicit recovery workflow.

## Gemini Decision Engine V5, disposable Writeback QA and recovery visibility

Gemini remains the decision-making agent for inventory corrections. The
`get_evidence_coverage` tool exposes per-sheet row counts, populated/numeric/
formula counts, and remaining read budget without leaking worksheet values or
selecting an edit. Gemini uses those signals to decide where to read next.
An automatic `NO_CHANGES_VERIFIED` assessment is only accepted when its
fresh, matching cell evidence contains at least two populated columns from
the same operational row (row 2 or later); one-cell or metadata-only claims
receive one guided reinvestigation and then review-required status.
This is an evidence-minimum gate, not an automatic decision that a row is
correct. Gemini still selects corrections and the backend validates target
identity, formula safety, current evidence hashes, and write readback.

Writeback QA is an explicit, permission-controlled action available from
Inventory → Configuration. It uses the *saved* source and archive folder
(or, if no Archive is configured, the source file parent folder),
creates a uniquely named fresh Drive copy tagged `cam_inventory_qa_disposable`,
adds a hidden QA tab **inside the copy**, writes a random marker, reads it
back, and moves that copy to Trash even when verification fails.
The original workbook is never a write target; the QA action is not part
of Inventory's daily scheduler. `POST /api/inventory/daily-sheet/writeback-qa`
requires Inventory control permission. Validate the archive folder and Google
OAuth Drive/Sheets permissions before running. The button requests explicit
confirmation and returns only status flags, not sheet contents or tokens.

Inventory Learning health reports `unverified_noop_streak` after three
consecutive completed, supposedly verified zero-write days that have no
credible multi-column row-level no-change evidence. A genuinely grounded
zero-edit day resets the count; missing evidence triggers a dashboard
warning rather than invented stock adjustments. Learning still requires
human review/activation for new business rules. No automatic backfill
of historical Morning Reset or Shared Sheet changes is triggered.

## Gemini-led Inventory Daily reconciliation

Inventory Daily keeps **Gemini as the sole agent selecting operational cells and
new values**. Approved knowledge from the tenant/workbook/sheet is included as
business guidance. The agent now also receives a bounded, read-only historical
learning context: consistent-but-unapproved patterns as hypotheses, conflicting
observation topics without disputed conclusions, and historical row-level
observations from independently verified audits. Old evidence must be checked
against the current workbook before a correction can be proposed. A no-op based
only on metadata gets one controlled opportunity to investigate more row-level
evidence (provided the tool-round budget allows it); another unsupported
metadata-only no-op is review-required, not a verified correction. The host
continues to enforce permitted writable Gemini-copy identity, evidence hashes,
protected/formula/merged cell exclusions, review-only semantic decisions and
readback verification. Audits record the learning-memory hash and observation
count. These changes **do not** approve contradictory learning proposals,
override carry-forward registry gates, retroactively run a reset or authorize
writes to the Shared Sheet.

## Material and warehouse readiness (no automatic Sheet mutations)

`GET /api/inventory/materials/readiness` is a tenant-scoped, read-only diagnostic
returning the active material and warehouse counts, pending material candidates,
duplicate normalized candidate names and stable blocker codes. Pending material
candidates are grouped for triage, not automatically approved or inferred as new
SKUs. In the Materials UI, a user with Inventory review permission can explicitly
create a named warehouse (`POST /api/inventory/materials/warehouses`). Creating a
warehouse does **not** write or reset a Google Sheet, does not certify its physical
identity, and cannot repair historical Morning Resets. Validate any entered
warehouse code/name against the actual source workbook before approving mappings.

Pending rows can now be approved in a strictly scoped group via
`POST /api/inventory/materials/candidates/{candidate_id}/approve-equivalent`.
The reviewer must choose an existing, active, same-tenant material and confirm the displayed rows and source count. A group
may span dated source snapshots but only within the same tenant, worksheet,
normalized name and normalized category. The server locks/rechecks every
candidate ID against the exact ID list seen by the reviewer. New, removed,
or replaced candidate rows, foreign material IDs and conflicting suggestions
fail closed. A successful action creates approved aliases/external identities
only—**no Google Sheets mutations or quantity changes**. The Materials screen
shows actionable cross-snapshot groups separately from global name-duplicate
statistics. No material identity is inferred solely from a matching name.

The isolated V4 write/readback regression uses two independent in-memory
workbooks. It verifies that a staged exact-copy edit changes only the test
copy, and that mismatching readback fails instead of being reported as a
verified write. It is **not** evidence of live Google Sheets write access;
that still requires a separately authorized disposable workbook test.

For live Agent V4 `apply_mode=auto`, an empty plan with only a generic
metadata/structural observation is now `review_required` with an
`no_actionable_assessment` issue rather than a misleading `completed`
reconciliation. A legitimate zero-write cycle must include explicit, evidence-
backed `NO_CHANGES_VERIFIED` row-level observations. The rule does not bypass
existing evidence readback, formula, protected cell or identity guards.
Historical completed zero-write audits remain unchanged.

No part of this rollout automatically turns existing material candidates into
approved registry rows, invents locations, runs old Morning Resets, or modifies
the Shared Sheet. The path to first verified write is: approve material and
warehouse mappings, generate an evidence-backed action plan, test against a
disposable workbook copy and verify readback, then enable controlled live writes
for a current operational day only.

## Continuous Inventory Learning V2

Completed, verified evening reconciliations now trigger two independent review-only
learners: verified cell-change history and grounded workbook assessments. The
assessment learner is deliberately useful for zero-write days and requires
at least two distinct verified business dates with cell-level evidence. It
groups by sheet and observation code. Different conclusions are recorded as
conflicting observations that require human investigation, not assumed true
business rules. No learning job can write to Google Sheets or auto-activate a
knowledge entry. Previously rejected conclusions do not reappear as new
proposals on the next run.

The original Inventory Knowledge approval workflow remains authoritative.
Learning results and safe failure classes appear in each operation audit under
`summary.learning`. The read-only
`GET /api/inventory/daily-sheet/knowledge/learning-status` returns verified
day count, verified cells read, latest learning status and candidate counts,
and knowledge statuses, without returning spreadsheet content.

## Learning health, contradictions and approval safety

The same endpoint also returns `health.status`, `health.issue_codes`,
`health.conflicting_proposals`, `health.consistent_proposals`, and the age
of the latest verified day. The Inventory Knowledge screen displays a warning
when the last learning run failed, a verified reconciliation is stale by at
least two local calendar days, or conflicting proposals remain unreviewed.
If the first post-deploy learning cycle has not run, the page shows a
`watching` state rather than claiming a successful learning run. These are
read-only dashboard alerts; they are not push notifications and do not
automatically retry or mutate Google Sheets.

A raw `verified_daily_assessment_v2` proposal with
`consistent_conclusion=false` cannot be directly activated (API returns
`inventory_knowledge_conflicting_observations_not_activatable`). Instead,
inspect the linked source evidence, write a corrected revision with a
non-conflicting documented rule, and explicitly activate that version. This
server-side guard also protects against accidental activation outside the UI.
Older conflicting proposed rows remain in the review queue unchanged.

Post-commit learning errors never roll back a verified reconciliation. If
a learner fails, the audit receives `learning.status=failed` and the
`cam.inventory.learning` logger emits a tenant-scoped warning without key
material. Running verified-assessment mining against existing audits after a
deploy performs only database-backed proposal creation; it does not call
Gemini or modify workbook files.

## Shadow run

Set INVENTORY_SHADOW_MODE=true for the pilot. Discovery through validation remains observable, but ledger commits and Excel/Drive export/archive effects are blocked with a stable inventory_shadow_mode_*_blocked result. Treat this as test evidence, never as a completed production export.

## Rollback / kill switch

Disable Drive polling first, then INVENTORY_WORKER_ENABLED, scheduler, and INVENTORY_AUTOMATION_ENABLED. Set the tenant Inventory processing control to paused and the Inventory AI emergency stop if needed. This preserves Inventory audit/history rows and does not touch Creative controls, queues, workers, or Google sources. Restart only Inventory containers after corrective action.

## Limitations

The API/provider gateway remains disabled without any permitted Inventory or same-tenant shared Gemini credential. Shared Image/Video keys require INVENTORY_GEMINI_SHARED_FALLBACK_ENABLED and retain their own project quotas; never copy decrypted keys into Inventory configuration.

## Overnight Gemini outage / Inventory Daily recovery (15 October 2026)

When the V4 Evening Reconcile Gemini call returns a retryable provider error,
the scheduler retains the existing persisted slot and retries at a **minimum
5-minute interval**. The evening slot has a bounded **72-attempt budget**
(instead of the 12-attempt general slot budget). On the next local business day,
the minute timer may continue that **existing previous-day Evening Reconcile
slot only before the configured Morning Reset time**. It must find a completed
previous-day snapshot with an existing Gemini copy and an already-created
reconciliation job. The live source workbook must still match the captured
Drive modification timestamp; otherwise the existing safety gate fails closed.
There is no retroactive creation of missing historic jobs, no automatic rewrite
of a shared working workbook, and no replay after the daily cutoff.

A `review_required` or `shadow` result is **not** proof of verified closing
inventory; the Evening Reconcile job now remains terminal with an explicit error
rather than incorrectly becoming `completed`. Provider errors with
`retryable=False` remain terminal even if a generic provider error name
otherwise resembles a transport outage. Human review of identity/ambiguous
cells, OAuth permissions, Gemini model configuration and verified evidence
remains necessary for those cases.

**Important:** On 2026-10-15, the previous day's Gemini reconciliation was not
verified, so the Morning Reset failed with
`previous_day_gemini_not_verified`. After the one-hour Morning Reset safety
window, **do not automatically backfill 2026-10-15**. The controlled manual
opening-only recovery process must first obtain verified prior-day evidence,
preview the exact changes and validate that current operational inputs and
formulas will be preserved. The deploy never directly edits the shared Sheet.

For operations, inspect the V5J minute timer and its journal and correlate
`inventory_daily_scheduler_previous_day_reconcile_failed`,
`inventory_daily_scheduler_morning_reset_failed`,
`inventory_gemini_request_failed` and
`previous_day_gemini_not_verified`. An `INVENTORY_V5J_ONESHOT executions=0`
line **does not prove that Inventory is healthy**; it can mean idle,
retry-delayed, review-blocked or missed safe window. Inspect the persisted
inventory slot jobs, carry-forward status, previous-day snapshot verification
and provider credentials to distinguish them.

## Durable historical Gemini recovery queue

Inventory Daily uses a **separate native systemd timer**
(`creative-asset-manager-inventory-replay-queue.timer`) for historical Gemini
replays. It is independent of the V5J lifecycle timer, so a long Gemini replay
cannot prevent the scheduled Morning Reset or Evening Reconcile from running.
The operator uses Inventory → Tiến trình kiểm kho → Gemini từ Snapshot sạch
(or Replay Sandbox). These controls enqueue a persisted, tenant-scoped
`inventory_v4_historical_replay_request` job instead of waiting for
`/gemini/replay` synchronously in the browser. Duplicate active requests for
the same tenant/day share a job; after browser reload the active job list can
be queried. Replay jobs retry transient Gemini errors with a minimum five-minute
backoff, up to 72 attempts, and show status/attempt count in UI. Permanent
permission/model/configuration failures remain visible as failed jobs.

The replay source is always a **fresh copy of the immutable date snapshot**,
not the Shared Sheet. Promotion requires independently verified Gemini output;
it updates the official Gemini copy pointer only. Automatic replay does
**not** run Morning Reset recovery or mutate Shared. If the current-day
Morning Reset is blocked by `previous_day_gemini_not_verified`, recover the
prior day's Gemini evidence first. Once verified, explicitly Preview the
current-day Opening-only repair; review the exact source/target cells and
plan hash; then Apply after approval. No automatic historical reset, no clear
of operator input or formulas, and no blind overwrite after the safety window.

API endpoints:
- `POST /api/inventory/daily-sheet/lifecycle-history/{date}/gemini/replay/queue` (control)
- `GET /api/inventory/daily-sheet/gemini/replay/jobs` (read)
- `GET /api/inventory/daily-sheet/gemini/replay/jobs/{job_id}` (read)

The old synchronous replay endpoint is retained for backward compatibility,
but the UI and batch replay use the durable queue.
