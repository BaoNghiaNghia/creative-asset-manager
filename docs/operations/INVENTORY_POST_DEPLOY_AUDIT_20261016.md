# Inventory post-deployment audit — 16 October 2026

Scope: CAM production at `4f62966e`, Inventory Daily/Gemini lifecycle and replay, Materials, disposable Google Sheets QA, authenticated UI and host runtime. **Read-only audit**; no historical replay, reset Apply, tenant mode change or Shared Sheet write was executed.

## Verified production evidence

- `/version` reports backend `4f62966e`, `/ready` reports PostgreSQL/Elasticsearch available; frontend build metadata points to source `e34a78f9`.
- Authenticated production browser smoke: 27/27 route checks, 0 failures, 55 nonblocking warnings (the smoke does not prove an actual Sheets write). The existing smoke plan checks Inventory overview; the distinct authenticated historical-diagnostics interaction is not yet independently verified on live data.
- API, four image workers, video workers, visual worker and isolated encoder are active; Inventory replay queue and V5J lifecycle timers are active. Observed V5J tick: `executions=0`; observed replay queue: `auto_enqueued=0 executions=0`, `slot_not_retryable=1`, `already_requested=1`. A running timer is **not** a repaired incident.
- Oct 14 evening: replay `completed / verified / promoted=true` (six attempts). Oct 13: `review_required / not_executed / promoted=false`, prior provider failure. Oct 08: `review_required / not_executed / promoted=false`, legacy unknown-tool safety error. The exact Oct 15 Morning Reset Apply/readback remains unverified.
- Disposable Google Drive/Sheets QA has **no independent authenticated live result**; no evidence warrants marking a shared workbook as write-ready.
- Host reports around 3 GiB MemAvailable but 1.6/2 GiB swap in use; `systemd-networkd-wait-online.service` remains failed from the documented provider IPv6 gateway issue. No automatic network or swap modification is permitted as part of this audit.
- UI visual manifest coverage: 6/11 profiles aligned; Inventory Materials has 12 unapproved comparison images; Inventory Daily has eight changed mobile images in a review-required proposal. Fixture/functional QA passing is distinct from accepting a visual baseline.

## Fix priority and gate

| Priority | Defect / risk | Remediation | Proof required |
| --- | --- | --- | --- |
| P0 | Unverified Oct 15 Morning Reset | Inspect persisted Closing Oct 14 and Reset Oct 15. Use explicit date-scoped Preview → Review, checking row identities, protected cells, formulas, warehouse/material mappings, and evidence hashes. Do **not** Apply an old Reset against a later live operating day. | Explicit authorized operator review and independent current workbook readback |
| P0 | Historical Oct 13/08 `review_required` | Inspect immutable snapshots, persisted slot codes and replay review codes per date. Do not blindly retry terminal review jobs or override the seven-day auto lookback. A scoped fresh-copy sandbox investigation may precede any explicitly approved promoting replay. | `verification_status=verified` and `promoted=true` for any promotion |
| P0 | Unknown Google Sheets live write authorization | Run Inventory Configuration → Writeback QA with Inventory Control in a real authenticated session: Drive disposable copy → marker write → readback → verified Trash GET; compare the original source's modified time before/after. Original must not be a write target. | All three literal flags: `verified`, `disposable_copy_trashed`, `source_file_unchanged_by_qa` true |
| P1 | Replay diagnostics could emit arbitrary persisted provider strings and accept truthy string `"false"` as promotion | Normalize diagnostic status/error codes to safe machine identifiers; require an actual Boolean `true` for reported `promoted`. Treat malformed `promote` intent conservatively. Do not mutate stored jobs. | Poisoned-code regression and unchanged legitimate replay semantics |
| P1 | Inventory isolation SQLite fixture missing shared Stage 4 control table | Create the model's shared-policy table inside fixture; do not alter production DB schema. | Isolation suite and full Inventory suite pass |
| P1 | Repeated idle timer diagnostics obscure actionable incidents | Replace once-per-minute full backlog dumps with a bounded change/heartbeat policy, preserving counters and the first/changed error. Avoid logging raw payloads. | Log retention/alert regression; no lost first failure |
| P1 | Catalog mappings incomplete/unknown | Reconcile warehouse identities and material candidate groups with workbook evidence. Require same-tenant review; no automatic alias approval or stock adjustment. | Explicit Material/warehouse readiness and verified source mappings |
| P1 | Insufficient production incident UI gate | Add authenticated, GET-only smoke for `/inventory/pipeline`, opening the diagnostics drawer and verifying date-specific response; report coverage separately from mock Browser QA. | Authenticated scoped GET 200, readable statuses, no POST |
| P2 | Visual baseline proposals pending | Review screenshot diffs of Inventory Materials and Daily mobile against intended new layout before accepting. Never force-update baselines to green. | Approved proposal with reviewers and 6-viewport visual rerun |
| P2 | Host wait-online failure and swap pressure | Diagnose IPv6 gateway with provider console before network changes. Capacity profile Visual encoder and worker under load; keep low-memory deployment drain. | Clean controlled reboot tests and stable `/ready` |

## Next controlled production run

1. Open Inventory Daily → select Oct 14, Oct 15, Oct 13 and Oct 08 → Bằng chứng trạng thái DB. Collect sanitized date-scoped status and identify the missing/reset blockers without executing jobs.
2. Confirm configured `agent.apply_mode` and immutable snapshot lineage; if the profile is in review/shadow, a promoting replay must fail closed.
3. Review Materials and warehouse candidates using source evidence; execute disposable Writeback QA **only** under an explicitly authenticated Inventory Control session.
4. Prepare a date-specific recovery Preview for Oct 15; halt if source workbook changed or the current operating date has advanced. Do not backfill an old Opening through a blanket Apply.
5. Only after authorized review, rerun eligible immutable-copy work for Oct 13/08; require independent verification before promotion. Recheck lifecycle, logs and Sheets readback.

**Limitations:** The production operator CLI diagnostics run through the external EnvironmentFile was blocked by the host policy in this audit. A `401` response without a login is expected RBAC, not proof of successful authenticated data inspection. No claim of verified Oct 15 Reset or Google Sheets write permission is made.
