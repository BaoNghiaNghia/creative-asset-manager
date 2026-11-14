# Implementation Roadmap

## 2026-11-14 — CAM Agent Harness V2 Phase 4C.3C: CI repair and cross-agent safety

- [x] Fix failing PostgreSQL 12/16 reclaim test by modeling the exact new claim timestamp and slot expiry, without loosening future-skew validation.
- [x] Block other-agent takeover of an unresolved job-bound slot without unique-key exception, and fail closed when a live slot outlives a shortened source lease.
- [x] Add PostgreSQL 12/16 negative-path tests for cross-device handoff, non-monotonic attempts, and shortened leases; merge current main into isolated candidate.
- [ ] Obtain green hosted PostgreSQL CI on candidate and document merge/release readiness. Migration 0209, dispatch, result wiring and Windows activation remain OFF pending independently verified off-host restore and approved canary.

## 2026-11-14 — CAM Agent Harness V2 Phase 4C.3B: claim-epoch fenced slots (code-only)

- [x] Bind each durable executor slot to `processing_jobs` job ID, claimed attempt count, and claim start timestamp; increment PostgreSQL bigint fencing tokens on authorized newer claim epochs.
- [x] Add default-OFF transactional reserve/renew/release and inert output-fence validation; prohibit stale token reuse and renewal by an expired prior attempt.
- [x] Extend existing PostgreSQL 12/16 CI matrix with concurrency, replay, recovery and tenant/security guards; no extra VPS test environments or provider calls.
- [ ] Obtain green hosted PostgreSQL CI, independently verified off-host production restore and release gates; **do not** activate or migrate 0209 on production before these checks.
- [ ] Integrate output-boundary fencing into the image-result handler and Windows relay behind a separate reviewed opt-in, with an idempotent receipt and actual browser/provider evidence; then consider controlled canary.
- Design: `docs/architecture/CAM_AGENT_HARNESS_V2_PHASE4C3A_FENCED_SLOTS_20261114.md`.

## 2026-11-14 — Etsy Spy: 22 named garment preview backgrounds

- [x] Add 22 requested swatches, preserve checkerboard and four original non-duplicate extras.
- [x] Centralize color values for picker, stage and thumbnails, with reference-only color disclaimer.
- [x] Validate the named colors in React tests, TypeScript and four responsive Firefox viewports.
- [ ] Confirm green hosted CI, merge source + committed frontend dist and release frontend with current + rollback; verify public health and authenticated Picked modal separately.


## 2026-11-14 — Etsy Spy Picked Design thumbnail auto-width follow-up

- [x] Match Design thumbnail height to Product/Etsy (68px desktop, 76px mobile); use intrinsic image width without forced square sizing.
- [x] Keep multi-design overflow inside the Design cell; reset conflicting products-table min width on mobile cards.
- [x] Verify 53 targeted React tests, full TypeScript and Firefox layout/interaction QA at four responsive viewports.
- [ ] Merge after green CI, deploy frontend only with one retained rollback, verify live build and public read-only smoke.


## 2026-11-14 — Etsy Spy Picked multi-position Design gallery (UI candidate)

- [x] Group actual PNG artwork by print location and variants in the modal, preserve the original PNG and source thumbnail hover in the table.
- [x] Add viewer metadata, navigation, background-preview labels and accessible desktop/tablet/mobile layouts without API/database changes.
- [x] Pass 52 targeted Etsy Spy tests and desktop/tablet/phone/landscape synthetic Chrome geometry checks.
- [ ] Pass locked CI, verify real Picked multi-placement data on production and deploy reviewed frontend with current + rollback retained.
- Reference: `docs/operations/CAM_ETSY_DESIGN_GALLERY_UX_20261114.md`.

## 2026-11-13 — Etsy Spy Search Card intent → CAM confirmed selection

- [x] Add authenticated, tenant/agent-scoped Pick/Later/Win Keyword write contract with durable idempotent CAM receipts; align source-owned Shop 20+ carts and listing review qualification.
- [x] Add nullable Shop evidence and receipt migration `0208_etsy_spy_agent_review_receipts`, rollback guards and negative-path tests.
- [ ] Pass hosted API CI including new Etsy Spy tests, migrate via reviewed production release from main and verify health/schema.
- [ ] After zero-lease paused Chrome boundary, activate matching Windows extension/backend and opt in Windows sync `review-sync-enabled.flag`; verify exact real CAM receipts.


## 2026-11-11 — Etsy TikTok Listing Pilot V2: manual-only activation, Gemini lane and retry

- [x] Decouple manual listing generation from Auto on Pick. The listing skill is available for authenticated manual pilots by default, while `ETSY_TIKTOK_AUTO_ON_PICK_ENABLED=false` guarantees no automatic work on new Picks.
- [x] Limit the normal manual Backfill button to five Picked rows per request, enforce the same server cap, and preserve tenant/RBAC checks and existing job idempotency.
- [x] Admit `etsy_tiktok_listing_generate` to the shared RRUGC Gemini model and pacing gates; require the worker's claim-time model/credential reservation and defer quota exhaustion without burning attempts.
- [x] Retry failed jobs explicitly at most twice, retaining bounded error history; prohibit cancelled or concurrency-held job revival.
- [x] Auto-refresh Picked listing statuses only while jobs remain active; support modal status polling and failure reason/Retry action. No per-row automatic polling in table.
- [ ] Production canary: queue one Picked product through an authenticated session, verify real output, wording QA, title/description limits, tenant scoped persistence, model quota and UI before `ETSY_TIKTOK_AUTO_ON_PICK_ENABLED=true`.
- [ ] Only after canary success consider increasing `ETSY_TIKTOK_BACKFILL_MAX_PER_REQUEST`, and connect YTrends MCP before declaring verified TikTok search keyword research.


## 2026-11-11 — Etsy Spy Picked → TikTok Shop US Listing Optimizer (code candidate)

- [x] Analyze user-provided TikTok Shop Listing Optimizer skill: photo + competitor title → Product Title, Description, 3–5 Highlights, Search Terms. Preserve 25–200/10000/1500/250 field limits and default manual QA.
- [x] Implement tenant-bound idempotent ProcessingJob execution using the existing Gemini worker, without touching Image Extension or creating another queue/table. Unpick fences and cancels existing jobs; retries must not duplicate.
- [x] Project per-Picked-row status into the existing Etsy Spy table; open the full four-field draft in a copyable modal, with bounded backfill and single-row queue endpoints.
- [x] Clearly label YTrends MCP absent and all search terms unverified; do not substitute SMS or invent U.S. shipping/production claims.
- [x] Add default-OFF `ETSY_TIKTOK_LISTING_ENABLED` flag, tests, documentation; no migration or new provider credential.
- [ ] Run full CI/frontend build, merge and review production release with flag OFF; then opt in one controlled pilot if Gemini quota and YTrends/QA limitations are accepted.
- [ ] Integrate authenticated YTrends MCP separately before marking any output as keyword-researched. Publish to TikTok Shop remains out of scope.
- Architecture: `docs/architecture/CAM_ETSY_PICKED_TIKTOK_LISTING_OPTIMIZER.md`.


## 2026-11-11 — CAM Agent Harness V2 Phase 4C.2A: stateless paired idle attestation

- [x] Add a default-OFF `harness-idle-attestation` endpoint on the existing paired Image Agent transport, with a **separate explicit idle feature flag** and a distinct HMAC domain from job heartbeat. The signed self-report expires after 30 seconds, has a device session UUID/sequence/nonce, and requires no unresolved CAM processing claim for the paired credential.
- [x] Add `harness-idle-verify` to validate a signed client-held receipt on the server and an RBAC-protected tenant-scoped admin verification endpoint. Both read CAM job ownership and fail closed on revocation, wrong tenant, stale signature or processing claim. Never print signing key, bearer, nonce, worker identity or source payload.
- [x] Extend paired `probe` with read-only `harness_idle_attestation_supported` and `harness_idle_attestation_ready` without creating work. Preserve existing Image Agent transport and job-bound heartbeat unchanged.
- [x] Windows client: strictly optional `harness_idle_attestation_enabled`, single-flight idle report every 15 seconds while journal absent, local short-lived client-held receipt, bounded backoff, redacted state and explicit two-step canary CLI. A journal with a job sends ONLY the existing per-attempt heartbeat.
- [x] Every reply still sets `verified_free_slots=0`, `dispatch_allowed=false`, `reservation_created=false`; device says it is idle but the browser session is NOT independently verified or reserved.
- [ ] Verify backend CI (including PostgreSQL 12/16), merge `main`, deploy backend with `--no-migrate --keep-releases 2`, then separately opt in the server and idle-only Windows relay, and perform real signed idle canary without claiming jobs.
- [ ] Phase 4C.2B: independently verified browser capacity plus **transactional** slot admission with rollback bridge, only after proven device evidence and user-controlled canary. No implicit auto dispatch.
- Architecture: `docs/architecture/CAM_AGENT_HARNESS_V2_PHASE4C2A_IDLE_ATTESTATION.md`.

## 2026-11-11 — CAM Agent Harness V2 Phase 4C.1: historical proof + bounded capacity evidence

- [x] Preserve the distinction between an active signed heartbeat and an **historical** HMAC-signed observation after a legitimate Image Agent result clears the ProcessingJob lease. Verify original tenant/owner/attempt/claim and completion timestamp plus accepted result receipt; never treat it as a fresh free slot.
- [x] Add tenant/RBAC-gated `external-executors/etsy-image/capacity-preview` with bounded 20-device/160-processing/32-completed sample, per-device signed busy/stale/conflicting-claim states, pending/retry counts, and at most five recently verified completed job IDs.
- [x] Keep source-of-truth `ProcessingJob` queue unchanged and `verified_free_slots=0`, `dispatch_allowed=false`, `reservation_created=false` for every response. Flag and secret remain behind existing controls; no new migration, worker, browser restart or provider calls.
- [x] Add negative checks for expired/completed/tampered/reclaimed proof, revoked agent, cross-tenant scope, read RBAC, emergency stop, conflicting leases, caps and retrospective validity after original signature expiry.
- [ ] Run CI, merge `main` and deploy backend-only with `--no-migrate --keep-releases 2`; check readiness, database 0207 and rollback.
- [ ] Phase 4C.2: separate idle-device signed handshake and actual atomic slot admission, only after first real authenticated job canary; default OFF until approved.
- Architecture: `docs/architecture/CAM_AGENT_HARNESS_V2_PHASE4C1_HISTORIC_CAPACITY.md`.

## 2026-11-11 — CAM Agent Harness V2 Phase 4B.2B: Windows heartbeat compatibility

- [x] Existing Etsy Image Agent `/claim` task and successful `/check-job` processing response now return authoritative `attempt_count` to support an optional, attempt-fenced Windows heartbeat, without changing legacy lease/recovery/result behavior.
- [x] Paired `/probe` includes `harness_heartbeat_supported` and `harness_heartbeat_ready` (both flag and independent signing key present) as a read-only handshake; no signing material or device credential is returned.
- [x] Windows workspace `D:\CAM Image Extension` adds default-OFF, single-flight 15-second client heartbeat with bounded backoff, validated non-dispatch ACK, tenant/attempt checks, legacy journal hydration, redacted read-only diagnostics and local Node regressions.
- [ ] Verify full CAM CI and Windows Node tests; merge backend PR and deploy only merged `main` with `--no-migrate --keep-releases 2`. Preserve all 9 service health gates and schema 0207.
- [ ] Separate operator-controlled live canary and server-only secret provisioning before enabling the client or backend flag. Never restart an active relay mid-job or treat local ACK as authoritative HMAC proof.
- [ ] Next: use authenticated lease-bound heartbeat to gate actual capacity/admission behind a separate default-OFF feature flag, after provider result lineage and rollback evidence are independently verified.
- Windows documentation: `docs/CAM_HARNESS_V2_PHASE4B2B_HEARTBEAT.md` in the separate Image Extension workspace.

## 2026-11-11 — CAM Agent Harness V2 Phase 4B.2A: authenticated external heartbeat

- [x] Reuse paired Windows Image Agent bearer and existing `ProcessingJob` per-attempt HMAC lease token; add a separate feature-gated `harness-heartbeat` endpoint without changing the legacy offline `check-job` recovery.
- [x] Require exact tenant/job/attempt/lease owner, picked source, non-cancelled processing state, bounded remaining lease, and server-only 256-bit signing key; persist one capped, short-lived signed proof on the owned ProcessingJob row under a row lock.
- [x] Add tenant/RBAC-gated read-only admin verification that fails closed on tampering, timeout, revoked device or reclaimed job; no token/worker/payload returned, no lease renew, slot reservation or dispatch.
- [x] Keep `CAM_AGENT_HARNESS_EXTERNAL_HEARTBEAT_ENABLED=0` by default and require a separate signing key; no client change or runtime activation until Windows Image Extension has been adapted and verified.
- [ ] Run local/CI security regressions, merge and deploy backend only with `--no-migrate --keep-releases 2` and feature OFF.
- [ ] Next Phase 4B.2B: opt-in Windows extension heartbeat loop, verify signed acknowledgments, use stale/missing heartbeat as a hard gate before real executor admission.
- Architecture: `docs/architecture/CAM_AGENT_HARNESS_V2_PHASE4B2A_EXTERNAL_HEARTBEAT.md`.

## 2026-11-11 — Production release gate: visual encoder warmup timeout

- [x] Phase 4B.1 PR #104 CI 16/16 PASS, merged at `395d60ee1`. First `--no-migrate` release activated this commit, but the script exited 1 because isolated SigLIP2/OpenVINO encoder needed approximately 36s versus the fixed 30s readiness window; schema compatibility PASS. Post-failure encoder `/ready` and 8 worker/encoder readiness endpoints returned 200 and nine services were active. This is not evidence that the first script finished.
- [x] Scope the longer 75-attempt warmup timeout to the **visual encoder ready** endpoint only, preserving strict /ready checks and existing 30-attempt API/worker gates. Add deployment-contract assertions.
- [x] PR #105 CI 16/16 PASS, merged at `ad462831c`; final `--no-migrate` backend release exit 0 (100%), 9/9 managed services active, 8/8 readiness probes HTTP 200, current `ad462831c`, rollback `395d60ee1`, PostgreSQL head 0207 and snapshot count 0.
- No new DB migration, feature activation, model/provider call, worker/queue architecture, credential or learning behavior.

## 2026-11-11 — CAM Agent Harness V2 Phase 4B.1: lease-bound advisory admission

- [x] Add tenant-only, read-only `ProcessingJob` lease observation; reject non-processing/expired/cancelled/misclaimed jobs, missing time/attempt evidence and stale lease-row activity without touching the queue.
- [x] Require pinned `job.read` tool grant and explicit request before advisory routing; map only verified Stage 1/2 RRUGC job types. Stage 4 remains unavailable pending independent handler mapping.
- [x] Keep no trusted Browser heartbeat, no executor slot lease, no generated job, and `dispatch_allowed=false`; an active DB lease observation does not prove worker heartbeat/authenticated provider output.
- [x] Add negative RBAC/tenant/stage/clock/cancellation/lease-boundary tests, architecture and rollback documentation.
- [x] PR #104 CI 16/16 PASS, merged `395d60ee1`; final backend release stabilized by PR #105 as above. All 9 services active, schema 0207 preserved, `395d60ee1` rollback retained.
- [ ] Next Phase 4B.2: scoped authenticated remote heartbeat and signed short-lived job/attempt/worker receipts for CAM Image Extension and other executors, followed by guarded actual admission.
- Architecture: `docs/architecture/CAM_AGENT_HARNESS_V2_PHASE4B1_JOB_LEASE_ADMISSION.md`.

## 2026-11-11 — CAM Agent Harness V2 Phase 4A: verified route preflight (deployed)

- [x] Refuse advisory routing from unverified executor status, positive-but-inconsistent capacity or a fabricated browser heartbeat; require exact verified capacity and coherent slot accounting.
- [x] Skip Codex account/CLI probes for unsupported stage/capability requests and browser-only agent definitions, while preserving tenant/RBAC checks and immutable agent versions.
- [x] Add explicit preferred/fallback advisory priority, maintaining `dispatch_allowed=false`; do not route real production jobs or enable AI policy promotion.
- [x] PR #103 CI 16/16 PASS, merged at `223e9c9f1`; backend-only `--no-migrate` production deployment exit 0, 9/9 services active, 0207 migration unchanged, rollback `1aa09620e` retained.
- [ ] Next: trusted worker heartbeat and ProcessingJob lease-bound executor admission, independently reviewed snapshot-bound QA and provider usage/cost receipts; only then consider measured routing optimization.
- Architecture: `docs/architecture/CAM_AGENT_HARNESS_V2_PHASE4A_ROUTE_PREFLIGHT.md`.

## 2026-11-11 — Phase 3C 0207 production completed (release record)

- [x] PR #100 merged to `main` at `1aa09620efad493c5e2889b5dd947de99df0958c`, CI 16/16 PASS.
- [x] Released with additive 0207 migration via reviewed backend script and distinct 0206 rollback bridge `81a8d582b`; exit 0, nine managed services active and all readiness probes healthy.
- [x] Production database read-only verification: Alembic head `0207_cam_agent_trial_artwork_snapshots`; snapshot table empty (0 rows). Snapshot writes, autonomous QA verdicts, learning and promotion not enabled by the release.
- [!] At owner's explicit request, independent off-host restore rehearsal was skipped for this rollout only. Production backup restorability is **UNVERIFIED**; older G1 restore gate documentation remains applicable for future changes that require verified recovery.

## 2026-11-11 — Phase 3C production rollout: 0206 bridge released

- [x] Merge PR #102 CI baseline and PR #101 0206 rollback bridge; both full CI 16/16 PASS.
- [x] Deploy exact bridge commit `81a8d582b` on production with `--no-migrate --keep-releases 2`; deployment schema compatibility check PASS; no 0207 DDL.
- [x] Verify live API version `81a8d582b`, API live/ready, PostgreSQL/Elasticsearch dependencies and nine active managed services plus worker readiness.
- [x] Record most recent backup timer success (2026-11-10 22:06 ICT) and capacity baseline (~4.5 GiB free, high swap usage); do not mistake successful backup service for off-host restore proof.
- [ ] Verify physical `current`/distinct `previous` release markers using an operator-approved scoped channel, plus independently restorable off-host backup and rollback readiness; host-policy filesystem restrictions must not be bypassed.
- [x] Add optional off-host restore count-parity checking (`--expect-table-count`) against exact required tables; fail closed on mismatch/invalid input and label unverified operator-supplied count provenance. [ ] Obtain independently verified source counts for the same backup snapshot and full off-host restore proof before clearing G1.
- [x] Wire synthetic real PostgreSQL source/dump/isolated-restore/count-mismatch exercise to existing PostgreSQL 12/16 CI jobs (no new VPS test environment). [ ] Verify green CI integration; [ ] conduct separately authorized full *production backup* restore off-host.
- [x] Independently re-run bridge deployment to full 100% success (exit 0); verify script-reported distinct protected current/previous, all API/worker probes, then clean its detached deploy-only worktree.
- [x] Execute the fail-closed, read-only G1 backup/restore preflight: local receipt PASS; off-host restore NOT_VERIFIED and same-volume restore capacity BLOCKED (database ~4.11 GiB; free ~5.42 GiB; 8 GiB reserve). Documented in `docs/operations/CAM_AGENT_HARNESS_V2_0207_RELEASE_GATE_20261111.md`.
- [x] Confirm latest PR #100 code+documentation CI completed 16/16 PASS (run #42955672526).
- [ ] Only after an independently verified *off-host* restore and operator-approved physical rollback/capacity proof may PR #100 be merged and 0207 applied with every snapshot/learning flag OFF; verify zero snapshot rows, Stage 1 Regenerate/Output History and Etsy SMS non-regression. Never delete QA evidence for rollback.

## 2026-11-10 — Phase 3C.3B2C2A: independently retained QA artwork snapshots

- [x] Add 0207 append-only `cam_agent_trial_artwork_snapshots` schema without a destructive Output History FK; downgrade refuses non-empty data.
- [x] Add independent default-OFF `AGENT_HARNESS_TRIAL_ARTWORK_SNAPSHOT_ENABLED` gate, strict manual dry-run/create contract, safe tenant/RBAC and provenance checks, durable reservation + unique idempotency, verified PNG/Drive copy.
- [x] Keep 0205 QA review table unwritten and A/B evaluation/promotion blocked. A verified snapshot is not a blinded QA verdict or trusted provider cost.
- [x] Stage an isolated 0206-compatible schema rollback bridge from current production commit before the new migration; sync strict nullability/FK/CHECK validation and PostgreSQL 12/16 scratch-schema canaries with PR #101.
- [x] Verify 0207 migration on both PostgreSQL 12/16 in isolated CI. [ ] Complete two-stage production release and independent service smoke; never migrate before the 0206-compatible bridge is the verified rollback release.
- [ ] Phase 3C.3B2C2B: separate snapshot-bound blinded review provenance/permissions and image cost receipts, then measured controlled study.


## 2026-11-11 — Phase 3C.3B2C2A: standalone 0206 rollback bridge hardening

- [x] Separate PR #101 contains only 0206-compatible code, negative-schema canaries, and CI coverage; **no 0207 migration or feature activation**.
- [x] Reject missing/extra/nullable snapshot columns, wrong exposure FK/delete policy, missing uniqueness, weak or permissive seal/size/dimension CHECKs, and unknown Alembic revisions.
- [x] Run 14/14 local bridge canaries and syntax/diff validation.
- [x] Verify 0206 bridge canaries and full PR #101 CI 16/16 PASS after PR #102; merged into `main` at `81a8d582b`.
- [x] Bridge deployed as a distinct no-migrate release; live 0206-compatible schema gate, API commit and all nine services verified. [ ] Independently validate physical rollback target and off-host restore evidence before migration.
- [ ] Only after bridge is retained: deploy additive 0207 with snapshot flag OFF, followed by independent QA and trusted cost receipts.

## 2026-11-11 — Agent Harness V2 release-gate CI baseline (PR #102)

- [x] Separate production-neutral test updates from migration 0206/0207; strengthen navigation module coverage and retain the real-runtime localhost URL prohibition.
- [x] Align Stage 0 Opportunity and Stage 2 gallery tests with the current 23-item Output Gallery (22 generated images plus the distinct Embroidery Reference).
- [x] Verify general worker refuses unknown/external-only job types without consuming or failing the pending job.
- [x] Reuse reviewed Skill Manager Browser QA assertions; run targeted API, frontend and Node checks plus TypeScript typecheck.
- [x] Align remaining stale AI Operations and Stage 2 tests with pipeline-only loads, Render Scene, ThreadTransfer V2 / stock imagery and on-open UGC Scout; targeted frontend 27/27 PASS.
- [x] Repair Creative Browser QA fixture/API status and outdated Stage 1/2 assertions, verify real headless Chrome across desktop/tablet/mobile + Research Inbox with zero functional issues.
- [x] Confirm PR #102 fully green (16/16 jobs), reviewed test-only diff and merge into `main` (`165259cc8`). Keep security and runtime gates intact.
- [x] Isolated 0206 bridge deployed with flags unchanged. [ ] Later 0207 migration remains gated on independently verified rollback/restore and capacity, flags OFF.

## 2026-11-09 — Phase 3C.3B2C1: actual Stage 1 artwork lineage / remote bytes

- [x] Build explicit, tenant-scoped read-only lineage: exposure receipt → completed Creative Brief and ProcessingJob → pinned 1/4/4 image generation job → output-version SHA-256 and source fingerprint. Invalidate stale/regenerated/other-tenant/other-study records rather than infer exposure from shadow.
- [x] Add secure evidence preflight GET and explicit remote-byte verifier POST, restricted to authorized admin scope. Verify Drive bytes, PNG encoding, dimensions and checksum with 16 MiB cap, retry-safe pre/post-I/O revalidation and no saved prompt/artwork bytes.
- [x] Add new `AGENT_HARNESS_TRIAL_ARTWORK_VERIFY_ENABLED` independent default-OFF flag for any Drive access. No AI provider calls, output mutations, database schema upgrade or new secrets.
- [x] Preserve `cam_agent_trial_qa_reviews` as unwritten: writes before reviewed-artwork retention could block current Stage 1 Regenerate through restrictive FK.
- [x] Verify 173/173 affected Agent Harness, Stage 1, lineage, authorization and error-path tests on the final revision; syntax/compile/diff checks passed. Unchanged 0205 migration/bridge tests also covered in broad pre-fix regression (all those cases passed).
- [ ] Deploy backend `main` with `--no-migrate`, verify services/rollback/flags.
- [ ] Phase 3C.3B2C2: immutable reviewed-asset snapshot retention, blinded independent reviewer credentials/assignment, QA writes and trusted provider usage/cost receipts; no evaluation/promotion before all are independently verified.


## 2026-11-09 — Phase 3C.3B2B: isolated challenger treatment and receipt

- [x] Add default-OFF `AGENT_HARNESS_TRIAL_CHALLENGER_ENABLED` gate, dependent on registration, shadow admission and baseline control capture with emergency-stop fail closed.
- [x] Map only preregistered Stage 1 Pick/Reject rule enums to fixed, human-reviewed typography directives (no freeform candidate prompt).
- [x] Worker preflight re-verifies tenant, claimed job, reserved study/assignment, deterministic arm, cohort and current candidate source before one Codex call; includes a distinct challenger directive while preserving baseline 1/4/4 schema.
- [x] After successful Codex, verify frozen plan and append immutable versioned challenger exposure plus SHA-256 prompt receipt in the completed Brief's transaction; no evidence on failure.
- [x] Validate 184/184 full Agent Harness / Stage 1 regression and adverse-scenario tests, compile and diff checks.
- [ ] Deploy reviewed `main` backend `--no-migrate` with feature flags untouched, check API/worker/rollback.
- [ ] Phase 3C.3B2C: establish authenticated immutable Stage1 artwork lineage, blinded independent reviewer with verified stored image bytes, trusted per-arm cost telemetry, safety approvals; **no automatic promotion** until all exist.


## 2026-11-09 — Agent Harness Phase 3C.3B2A baseline/control exposure receipt

- [x] Add default-OFF `AGENT_HARNESS_TRIAL_CONTROL_CAPTURE_ENABLED`, separate from existing 3C.3A shadow admission.
- [x] Add internal post-success Creative Brief worker receipt with DB-authoritative tenant/job/study/keyword/lease validation, deterministic frozen control arm and exact prompt digest, same-transaction audit and immutable/idempotent insert.
- [x] Keep challenger reservations unexposed; no candidate policy activation, new AI requests, feedback-pick reuse or automatic promotion.
- [x] Add tenant-scoped read-only `/trials/stage1/{study_id}/evidence` audit, showing counts and blocked evaluation (not QA quality claims).
- [ ] Final regression/security tests, deploy `main` backend `--no-migrate`, health and rollback checks; no production flag changes.
- [ ] Phase 3C.3B2B: pre-reviewed challenger-specific treatment on actual Stage 1 worker and genuine independent blinded QA on saved design outputs, with verified cost telemetry.


## 2026-11-09 — Phase 3C.3B1 PostgreSQL startup incident follow-up

- [x] Fix PostgreSQL-normalized 0205 CHECK introspection in `6db036aa6`, preserve structural fail-closed enforcement and add dialect-rewrite regression tests.
- [x] Re-validate PostgreSQL 0205 production schema and recover healthy API/managed workers without database downgrade or feature activation.
- [ ] Establish second 0205-compatible production release so rollback never selects the broken pre-fix backend; clean releases only after verifying both pointers.
- [ ] Add disposable PostgreSQL migration/startup gate to CI and a pre-activation rollback-release compatibility check.
- [ ] Proceed to verified treatment-aware worker receipts and blinded independent QA only after safety gates.


## 2026-11-09 — Agent Harness Phase 3C.3B1: exposure and QA evidence schema

- [x] Add migration `0205_cam_agent_trial_evidence` for tenant-scoped worker-applied exposure receipts and independent blinded QA reviews. Add SQLAlchemy mappings, composite FKs, uniqueness and nonempty-downgrade refusal.
- [x] Keep both ledgers empty, with no backfill from shadow admission, no write API or worker hooks, no policy change, no cost telemetry claim and no automatic promotion.
- [ ] Run isolated 0205 migration/rollback and 0204 bridge regression tests; deploy with migrations only after bridge `610b3ddda` is verified production current and compatibility confirmed.
- [ ] Phase 3C.3B2: real verified worker treatment receipts and independent QA/cost pipeline, then statistically sound holdout evaluation and signed owner review.


## 2026-11-09 — Agent Harness Phase 3C.3B0: immutable evidence rollback bridge

- [x] Define prospective 0205 exposure + blinded independent QA ledger contract with tenant-study-keyword lineage, per-job/output uniqueness and bounded verdict constraints.
- [x] Prepare 0204 backend startup compatibility for exactly `0205_cam_agent_trial_evidence` only when structural integrity is intact; reject missing/weak/unknown successors.
- [x] Preserve 0204 as current migration. No actual experiment activation, new job, worker routing, automatic promotion, provider cost or independent QA claims.
- [ ] Verify bridge on SQLite scratch database, migration regression and security tests; deploy backend-only from `main` with `--no-migrate` and all flags unchanged.
- [ ] Phase 3C.3B1: add separate 0205 migration + models **after** this bridge is live; capture actual worker-applied treatment only with provenance, separate from shadow-only admission.
- [ ] Phase 3C.3B2: blinded reviewer provenance + output/cost telemetry, read-only held-out evaluation and independently approved rollback/rollout.


## 2026-11-08 — Agent Harness Phase 3C.3A: pre-generation shadow admission

- [x] Add independent default-OFF `AGENT_HARNESS_TRIAL_ADMISSION_ENABLED`; additionally require existing READ, MANAGEMENT, registration gates and no emergency stop.
- [x] On the first Stage 0 Pick/Stage 1 Creative Brief enqueue, revalidate reserved keyword's tenant, cohort, frozen candidate digest and deterministic arm; reject prior generation and stale reservations.
- [x] Persist shadow-only (NOT actual exposure) manifest on the existing ProcessingJob and actor audit atomically; never change prompts, run agents or promote candidate.
- [x] Add negative tests for mutated keyword/arm, cancelled study, stale source, historical Brief, opt-out, repeated queue.
- [ ] Deploy with all flags OFF after full security/integration checks, verify production Alembic 0204 and backward-compatible release, health and rollback.
- [ ] Phase 3C.3B: authoritative applied-treatment exposure, independent blinded QA/cost telemetry and owner-reviewed rollback/promotion. Never count a shadow assignment as objective QA.


## 2026-11-08 — Agent Harness Phase 3C.2: inert study registry and keyword reservations

- [x] Add additive schema 0204 for tenant-scoped immutable study registration, candidate-source digest, and unique prospective keyword arm reservations.
- [x] Add strictly gated (default OFF) authenticated study/keyword APIs: dry-run first, immutable version, candidate-current verification, Stage1 generation not yet queued, and same-transaction operator audit.
- [x] Keep registered/reserved distinct from active/exposed. No Stage 1/2 job routing, learner promotion, background agent, QA claim or paid model calls.
- [x] Rollback bridge e130f82d4 was deployed BEFORE 0204 migration; prior backend validates exact additive tables/FKs/uniqueness.
- [ ] Validate PostgreSQL 0204 migration, head, backward-compatible bridge, and test canary before allowing operator registration flags.
- [ ] Phase 3C.3: pre-generation Stage0/1 trial admission, immutable exposure + independent QA telemetry, signed owner promotion and gated rollback.


## 2026-11-08 — Agent Harness Phase 3C.2 rollback bridge

- [x] Predeploy database startup compatibility for exactly one future, structurally validated 0204 holdout schema without changing Alembic HEAD or any live job.
- [x] Reject missing trials/exposures, missing composite tenant FKs, missing cross-study keyword uniqueness, or arbitrary future revisions.
- [ ] Add schema+registration API in separate release after this bridge is production-current. Rollback to this bridge release must pass at 0204.
- [ ] Keep actual challenger generation, trial activation, QA recording and promotion disabled until an independent provenance-controlled experiment is reviewed.


## 2026-11-08 — CAM Agent Harness Phase 3C.1 (prospective holdout preflight)

- [x] Add deterministic tenant/cohort/trial/keyword-level assignment kernel for a future control/challenger trial; assignments are planning-only, NOT registered or activated.
- [x] Add no-provider QA/holdout contract with independent-provenance attestations, keyword-level minima, conservative Wilson confidence intervals, critical-defect/cost checks and manual-review-only favorable outcome.
- [x] Add default-off read-only evaluation-readiness endpoint derived from existing Stage 3B.2 tenant-scoped source audit. No new migration, database write, execution, or automatic promotion.
- [ ] Persist immutable prospective registrations and arm exposure before generation, derive QA telemetry from independent reviewers, then evaluate true held-out outcomes.
- [ ] Add audited owner approval and canary rollback only after real evidence and security gates; do not apply learning to current jobs.


## 2026-11-08 — Agent Harness V2 Phase 3B.2: read-only feedback evidence audit

- [x] Add tenant/RBAC-guarded Stage 1 candidate Evidence Audit (no new schema), with explicit source-current/stale/rejected/promoted review statuses and at most 30 results.
- [x] Re-derive current trusted source votes against stored candidate identity without leaking keywords, asset hashes, reviewer data, prompts or output IDs.
- [x] Keep independent QA, holdout, automatic promotion and agent execution hard-off; preserve current Stage 1/2 jobs and existing feedback outbox.
- [ ] Canary the authenticated read flag separately after deployment verification and operator role check; never activate self-learning by default.
- [ ] Phase 3C: independently evaluated holdout + objective QA and audited policy review/promotion with reversible scope. Do not treat user Pick ratios as objective quality proof.


## 2026-11-07 — Agent Harness Phase 3B.1: durable incremental feedback

- [x] Reuse existing processing outbox ledger; on Stage 1 Pick/Reject/Undo change, append an idempotent tenant-scoped event in the same transaction. No event on repeated identical feedback or unclassified keywords.
- [x] Add opt-in operational scheduler to consume only its outbox event type with PostgreSQL SKIP LOCKED, bounded 24 events / 3 cohorts per 120s. Do not add model calls, ProcessingJob types or new migrations.
- [x] Recompute current Stage 1 verified-source evidence, revoke stale cohort candidates including undone votes, and persist new snapshots only as non-promotable candidates for registered disabled feedback-only agents.
- [x] Add two separate default-off flags for capture and processing, retain existing emergency stop; current execution and manual promotion remain disabled.
- [ ] Deploy backend-only to production after CI and regression verification; keep flags off, check worker health and current + rollback releases.
- [ ] Phase 3B.2: controlled production canary plus independent QA/holdout, dashboard and approved promotion with source revalidation.


## 2026-11-07 — Agent Harness Phase 3A: Stage 1 Feedback Intelligence

- [x] Reuse Stage 1 persisted Pick/Reject and keyword cohort classifier for a bounded, tenant-authoritative candidate evidence snapshot; no new queue, migration or provider.
- [x] Verify persisted artwork hash, output-to-job lineage, actor/timestamp, technical QA, distinct keyword and sample thresholds; deduplicate source assets and cap per keyword.
- [x] Add default dry-run admin preview and explicit audited candidate sync, behind READ + MANAGEMENT + separate feedback-sync feature flags, with no promotion or execution path.
- [x] Test tenant/RBAC, missing sources, undo, stale candidate prevention, idempotency, contradictory/duplicate votes, failure rollback and emergency stop.
- [ ] Deploy Phase 3A backend-only after CI review and production health checks; keep source votes and worker queues untouched.
- [ ] Phase 3B: event outbox for automatic incremental capture, stale snapshot revalidation and independent QA/holdout-controlled manual promotion.


## 2026-11-07 — Agent Harness Phase 2C.2 executor readiness

- [x] Add tenant-scoped Codex CLI account-pool readiness using existing enabled profile login status and FLOCK slot probes; zero new job execution or queue changes.
- [x] Add explicit fail-closed Browser Extension status until verified website provider, output lineage and durable Windows acknowledgements are available.
- [x] Add version-pinned, capability/tool-permission checked route preview with agent concurrency upper bound, emergency-stop awareness and separate flags; never returns dispatch permission.
- [ ] Release backend-only to production after tests and CI review; confirm latest /version and /ready, API and workers, keep current + previous and leave READ/execution flags default off.
- [ ] Future: trusted actual worker heartbeat and job lease, browser extension end-to-end test, verified Stage 1 Pick/Reject candidate ingestion.


## 2026-11-07 — CAM Agent Harness Phase 2C.1

- [x] Implement metadata-only, version-pinned Agent Catalog registration (Codex/Browser preferences), strict capability/tool allowlist and concurrency limits.
- [x] Enforce existing ai_provider.configure RBAC and tenant scope, separate default-off management flag, immutable/idempotent versions and same-transaction actor audit.
- [x] Add negative HTTP tests: disabled flag, unprivileged/cross-tenant, invalid payloads, audit failure rollback and safe response.
- [ ] Deploy backend-only to production with unchanged schema and existing Stage 0–5 pipeline; verify /ready, /version, worker health and previous rollback.
- [ ] Canary management flag separately with authorized tenant admin; no executor connection, feedback promotion or automatic execution until independently tested.


## 2026-11-07 — Agent Harness V2 Phase 2

- [x] Add disabled-by-default typed API and execution flags, tenant-scoped ORM/repository, immutable agent version registration and read-only authenticated overview; no worker or API write path. Phase 2A can ship without a schema change.
- [x] Detect migration collision with newest Etsy Spy `0202_etsy_spy_later`; supersede the old Agent Harness `0202` draft with a unique `0203` successor.
- [x] Implement narrow two-release rollback bridge for the reviewed additive `0203` schema; reject unknown revisions and missing tenant-key foreign keys. Validate with negative tests.
- [x] Deployed bridge backend-only (`--no-migrate`) as `0ca25dd83`; `/version`, `/ready` and worker health verified, with previous release retained.
- [x] Renumbered prepared Agent Harness schema to additive `0203_cam_agent_harness_store` after Etsy Spy `0202`; tested isolated upgrade/downgrade and schema-compatibility checks.
- [ ] Deploy `0203_cam_agent_harness_store` after passing CI PostgreSQL migration checks; preserve bridge as rollback and keep execution flags off.
- [ ] After schema activation, canary read API separately, then add authorized writes and verified Experience ingestion without changing job routing.


## 2026-11-07 — CAM Agent Harness V2 (Phase 0/1)

- [x] Establish version-pinned, capability-based in-memory Agent Registry and a standalone verified-experience promotion gate. 14/14 standard-library tests passed; neither module connects to workers, API routes, queues, or a new database.
- [x] Write consolidated 22-item work plan and read-only source audit: `docs/architecture/CAM_AGENT_HARNESS_V2_MASTER_PLAN.md`, `docs/architecture/CAM_AGENT_HARNESS_V2_PHASE0_AUDIT.md`.
- [ ] Independently verify live release/current+previous rollback, Stage2 engine distribution and Scout 4-agent network/CPU baselines before activation.
- [ ] Add tenant/RBAC-secured persistence, event lineage verification, and disabled-by-default feature flag before integrating agent dispatch or learning.


## 2026-11-02 — GatorHats Products Slot 1 two-output V2.3

- [x] Checkpoint verified photographic Embroidery Reference before editing front stock.
- [x] Display persistent Embroidery Reference plus front cap after inpaint, including failed/partial state and output version history.
- [x] Retry inpaint without new material ImageGen, Full Regenerate creates both images, preserve V2.2 job identity/version compatibility.
- [x] Tenant-scoped original/thumbnail URLs and basic backend/frontend tests.
- [ ] Inspect representative live inpaint output closely for low relief and panel curvature after a user-triggered regenerate.

Runbook: `docs/operations/CAM_SLOT1_EMBROIDERY_REFERENCE_INPAINT_V23.md`.


## 2026-11-02 — TikTokShop full source and reference replay

- [x] Reuse existing source V5 upgrade and 12-job inflight budget for all 66 source photos.
- [x] Add a bounded, restart-safe replay for V11 references only after the matching V5 source/campaign is ready.
- [x] Reuse Managed Drive originals when reapproved, do not upload duplicates or delete past generations.
- [x] Prioritize photo-composition search for exact apparel design zones with the existing Windows v58 Scout.
- [ ] Confirm live source/candidate queue decreases and healthy TikTokShop Scout claims continue after release.

## 2026-11-02 — TikTokShop Apparel Placement-Aware Scout

- [x] Detect collar, center/left/right chest, sleeve and upper arm artwork in the existing source-image Gemini analysis.
- [x] Reuse existing candidate Gemini analysis for per-zone visibility, occlusion, front/back pose and garment frame size; no additional AI call.
- [x] Reject unusable front/primary design views and require manual review for uncertain/secondary areas; store reasons and zone fit for Stage 3.
- [x] Protect TikTokShop Stage 4 from unverified reference photos while honoring explicit manual Good votes.
- [x] Keep TikTokShop placement-isolated grouping, bounded v5 source reanalysis and GatorHats behavior unchanged.
- [ ] Visually inspect sampled real TikTokShop source designs and candidate outputs after v5 reanalysis starts.

Runbook: `docs/operations/CAM_TIKTOKSHOP_APPAREL_PLACEMENT_SCOUT.md`.


## 2026-11-02 — Reuse Slot 1 native fine-thread material throughout Stage 2

- [x] Persist the byte-verified native ImageGen Pass A macro once per completed V2 Slot 1 design.
- [x] Slot 2/3 inpaint against the original verified 3/4 and right-side factory photos; protect the non-embroidery pixels.
- [x] Slot 4 keeps the original blank rear stock without generating new lettering.
- [x] Multi-hat and UGC Skills retain their scene-composition task; completed photos receive one further native thread inpaint pass with anti-reshoot QA.
- [x] Products Slot 7 uses one native inpaint per Natural-series color; no old custom ZIP dependency.
- [x] Make UI/run-readiness and Skill Manager reflect 5 built-in slots and only 2 configurable scene Skills.
- [ ] Complete human visual inspection of newly generated real-provider 3/4, side, multi-hat, UGC and colorway outputs.
- [ ] Investigate historical fixture visual-baseline sidebar drift separately; never auto-accept.

Runbook: `docs/operations/CAM_STAGE2_THREAD_REFERENCE_REUSE.md`.


## 2026-11-02 — Stage 2 Slot 1 Reference-First Thread Inpaint

- [x] Keep two native passes: photographic embroidery material, then original-stock crown inpaint with bounded thread-only composite and QA.
- [x] Remove redundant flat preview upload and duplicate Pass A reference from the new built-in path; reject unchanged Pass A before Pass B.
- [x] Unlock built-in Slot 1 in the UI without an assigned Skill and remove its obsolete Manage Skill action; preserve pinned legacy jobs and outputs.
- [ ] Verify actual native ImageGen output against approved photographic thread example after normal deployment; no automated unit test establishes visual parity.

See [Slot 1 pipeline contract](docs/operations/CAM_STAGE2_SLOT1_REFERENCE_INPAINT.md).

## Creative Brief V1 modal UX — 2026-10-30

- [x] Replace the sparse un-analyzed modal with a clear keyword briefing CTA, three concept-route previews, and preservation notice.
- [x] Make queued/running/failed states explicit, using indeterminate progress and retry without triggering ImageGen.
- [x] Present completed strategy insights and an interactive three-concept comparison with typography, embroidery feasibility, and risks.
- [x] Preserve focus return, Escape, close, polling, and phone/tablet/desktop layouts; add targeted component tests and three-viewport fixture QA.
- [ ] Review historical RRUGC sidebar visual baseline drift separately through the governed proposal workflow; do not alter baselines as part of this feature.


## GatorHats 8869 ZIP photo synchronization — 2026-10-24

- [x] Parse official color/angle tokens from ZIP filenames and directory names, with bounded image validation.
- [x] Preview complete, incomplete, duplicated and unmatched photo groups before import.
- [x] Store complete stock sets in immutable versions outside the release checkout and publish manifest atomically.
- [x] Wire versioned references into the Embroidery Design 2x2 gallery, Ecommerce image inputs and future 13-color jobs without reprocessing historical outputs.
- [ ] Verify the user's original 8869 ZIP name matches and execute the first live image sync after importing its bytes.

See [GatorHats stock sync](docs/operations/GATORHATS_HAT_STOCK_SYNC.md).

## Job-growth prevention — Source Sync 3-minute cooldown (2026-10-23)

- [x] Trace 28,186 scheduled source sync jobs over seven days to the 60-second poll loop (3 sources).
- [x] Add an indexed, DB-read-only 180s cooldown after successful incremental syncs, with no migration.
- [x] Exempt first synchronization, manual/full, daily full, cursor-bootstrap, failure/retry and continuation jobs.
- [ ] Monitor steady-state source update latency and source_sync job count in production before tuning the interval.

## Disk pressure — phase 2 fail-closed production rollout (2026-10-23)

- [x] Keep legacy job identities/idempotency intact; inspect live FK/index dependencies.
- [x] Add private remote-backup receipt published only after checksum-verified workflow success; never equate with tested restore.
- [x] Refuse to schedule/execute retention in production without >=8GiB free and a recent remote backup receipt; keep flag OFF by default.
- [x] Include fixed read-only disk-watch service/timer in the reviewed backend deployment workflow.
- [ ] Verify isolated restore of recent production dump, disk >=8GiB and activate the bounded retention canary.
- [ ] Review job-history tombstone/archival proposal and controlled index rebuild separately; do not mass-delete 646k jobs.

## Disk pressure P0/P1/P2 safeguards — 2026-10-22

- [x] Add opt-in tenant retention canary for completed job payloads only, without deleting job identities or changing the default disabled production policy.
- [x] Recover from partially created cleanup runs if queue enqueue fails; protect job idempotency and keep errors redacted.
- [x] Harden Codex/video proxy temporary cleanup observability and reject unsafe attempt paths.
- [x] Add retry for transient Google Drive *reads* in legacy Stage 1 retention; never retry deletion.
- [x] Add lightweight disk threshold monitor and optional systemd timer (source only, not automatically installed).
- [ ] Approve and activate low-rate retention canary after validated backups/free-space, then verify count-only metrics.
- [ ] Design a separate reviewed PostgreSQL job-history archival/tombstone migration; never mass-delete existing job rows.
- [ ] Schedule standalone review of orphaned video-final checkpoints, safely mapped to committed remote objects.

Runbook: [Disk pressure and retention operations](docs/operations/DISK_PRESSURE_RETENTION_20261022.md).

## Inventory business audit (2026-10-21)

- [x] Make stale source/readback conflicts terminal for human review; preserve technical retries and active leases.
- [x] Block the legacy Catinat automatic/manual carry-forward writer using exact Q47 configuration/source/target worksheet identities.
- [x] Add manager-authorized, evidence-bound atomic material bulk review and protect approval audit metadata against stale candidate/evidence updates.
- [x] Correct UI authorization/request lifecycle and add staff/manager fixtures and targeted regressions.
- [x] Record Inventory API 820 passed, frontend 814 passed across 146 files, analyzer 86 passed, typecheck/build PASS.
- [x] Pass all 30 compiled strict/full staff/manager browser states on phone, tablet, and desktop, with zero recorded issues and zero viewport overflow.
- [x] Correct and verify click-open / Enter-close disclosure capture in the Overview QA plan; preserve tested runtime source and dist (targeted planner tests 14/14).
- [x] Prepare governed 15-image Overview proposal `20261021T150736Z-b1a9bc23-inventory` from the actual reviewed change, with zero browser/runtime issues and preserved legacy profile coverage.
- [x] Record explicit user authorization and complete normal baseline acceptance for `20261021T150736Z-b1a9bc23-inventory` (2026-10-21 15:23:18 UTC / 22:23:18 Vietnam): strict/baseline-accept **15/15 matches under unchanged tolerances**, zero runtime/step/HTTP/unclassified request errors, and 3 classified identity aborts. Preserve exact reviewed hashes for all 15 accepted PNGs and all 12 legacy PNGs; acceptance commit A `3a2dc90091bca52e313c319c20bdbd3e5b406081` is recorded.
- [ ] Establish live backend provenance/schema compatibility and record real PostgreSQL concurrency validation limits.
- [x] Record historical source S `955af39383ba80ce0680f428c786cb1191aef8b9`, its exact-source build, and artifact R `d06995e9f435904e2cdec9ed1189c3b34164dad9` with verification evidence; baseline acceptance does not change the tested runtime.
- [ ] Restore GitHub HTTPS write authentication on the CodeLocal host, publish the review branch, obtain actual CI, and follow the reviewed clean-checkout release workflow after the remaining gates; push is blocked and no deployment has occurred.

Details: [Inventory business audit — 21 October 2026](docs/operations/INVENTORY_BUSINESS_AUDIT_20261021.md).

## Browser Agent recovery candidate 0.2.12 / Manager 0.1.43 (2026-10-21)

- [x] Reproduce and repair parser counts, successful-prefix loss, serial-batch continuation and same-tab/popup refusal handling.
- [x] Preserve immutable reports on cleanup failure and retain uncertain profile ownership for operator review.
- [x] Serialize standby Start/Stop/retry/update decisions; verify six isolated Rust lifecycle-policy tests.
- [x] Verify all 93 worker tests (including 20 recovery regressions), three release-builder tests, extracted-ZIP smoke, Manager frontend tests/build, version preflight and scoped staging gate.
- [x] Unify package/report/heartbeat provenance and correct Start-versus-browse instructions.
- [ ] Complete native Windows/Tauri build and real approved browser acceptance; do not equate source or heartbeat with deployed scanning.
- [ ] Deploy the verified candidate through the existing Windows release workflow; production scan dispatch remains disabled.

## Branding DNA Windows runtime (2026-10-20)

- [x] Implement paired live CAM heartbeat launcher with read-only shop/job diagnostics and no duplicate local shop configuration.
- [x] Add unit tests for token hygiene, real CAM protocol shape, and fail-closed dispatch enforcement.
- [ ] Start and verify persistent launcher on `DESKTOP-3FR0LF0` when CodeLocal reconnects.
- [ ] Verify marketplace-specific authorizations, source rights and shop activation one-by-one.
- [ ] Add independently reviewed worker lease/dispatch protocol before any unattended Amazon/Etsy crawling.


## Foundation

- [ ] 00 Codebase audit
- [ ] 01 Architecture documents and feature flags
- [x] 02 Provider contracts

## Asset ingestion

- [x] 03 Asset registry
- [x] 04 Content hashing and deduplication
- [x] 05 Processing jobs and outbox
- [x] 06 Incremental source synchronization
- [x] 07 Secure external downloader
- [x] 08 Managed asset storage

## Metadata and search

- [x] 09 Dynamic metadata persistence
- [x] 10 Metadata traverser
- [x] 11 Metadata normalizer
- [x] 12 Search projection builder
- [x] 13 Elasticsearch v2 index
- [x] 14 Search query parser

## AI and APIs

- [x] 15 Single-asset AI analysis
- [ ] 16 Pilot evaluation
- [ ] 17 AI batch processing
- [x] 18 External ingestion API
- [x] 19 Metadata sidecar export
- [x] AI-MULTI-01 Multi-provider AI registry and persisted provider resolution
- [x] AI-MULTI-02 OpenAI single-image Responses API
- [x] AI-MULTI-03 OpenAI Batch API
- [x] AI-MULTI-04 Provider, processing-mode, and model request API
- [x] AI-MULTI-05 Correct single/batch enqueue orchestration and bulk API

## Operations and product

- [x] 20 Projection rebuild and reindex
- [ ] 21 UI and admin operations
  - [x] 21A Processing status badges
  - [x] 21B Asset details
  - [x] 21C Search syntax
- [x] 22 Controlled rollout
- [x] 23 Worker runtime, health checks, and graceful draining
- [x] 24 Gemini single-asset metadata analysis
- [x] 25 Durable end-to-end asset pipeline
- [x] 26 Tenant-scoped rollout, tenant-filtered claiming, and pause controls
- [x] 27 Pilot evaluator, AI cost metrics, and AI budget circuit breaker
- [x] 28 Durable AI batch submission, polling, import, and selective retry

- [x] 29 Asset details UI, Search v2 UI, and authorized async operations

- [x] 30 Persistent encrypted OAuth tokens and distributed sessions

- [x] 31 PostgreSQL and Elasticsearch integration CI plus end-to-end pipeline tests

- [x] 32 Reconciliation generation markers, sensitive URL retention, and cleanup jobs

- [x] 33 Deterministic active analysis, search shadow comparison, and safe index lifecycle
  - [x] 33R1 Active-analysis integrity and deterministic rebuild ordering
  - [x] 33R2 Shadow-search safety, metrics, and reporting remediation
  - [x] 33R3 Elasticsearch lifecycle verification, recovery, and safe cleanup remediation

- [x] AI-MULTI-06 Frontend provider, model and processing-mode selection
- [x] AI-MULTI-07 Multi-provider production governance
- [x] AI-OPS-01 Tenant-scoped AI Operations dashboard APIs
- [x] AI-OPS-02 Safe tenant AI Operations control APIs
- [x] AI-OPS-03 AI Operations dashboard UI, interactions and bounded refresh
- [x] AI-OPS-04 Provider and tenant configuration UI
- [x] AI-OPS-05 Performance, bounded exports and final validation
- [x] AI-OPS-CI-FIX Migration and durable pipeline CI regressions
- [x] PIPELINE-OPS Tenant-scoped end-to-end Google Drive processing operations overview
- [x] Pipeline Overview compact operational layout (grouped summary/context cards)

- [x] 21D Toggleable file details and activity inspector

## Authentication and authorization

- [x] AUTH-01 Application users and external identity persistence
- [x] AUTH-02 Durable tenant membership and active tenant resolution
- [x] AUTH-03 Tenant-scoped roles and permissions
- [x] AUTH-04 Central application principal and permission dependencies
- [x] AUTH-05 OAuth application login, session rotation and tenant switching
- [x] AUTH-06 Tenant membership and role administration APIs
- [x] AUTH-07 Access Management frontend
- [x] AUTH-08 Durable RBAC for AI Operations and related admin routes
- [x] AUTH-09 Authentication/RBAC migration tooling and final validation
- [x] ADMIN-SETUP-SCRIPT Safe interactive first-administrator bootstrap
- [x] AUTH-JIT Secure Google and Microsoft self-signup provisioning

## Deployment

- [x] DEPLOY-COMMITTED-FRONTEND Committed Vite bundle, Docker backend and native Nginx/PostgreSQL deployment

- [x] PROD-DOCKER-01 Immutable Docker backend and production Compose
- [x] PROD-FE-02 Reproducible committed frontend release
- [x] PROD-VPS-03 Native Nginx and VPS deployment/rollback tooling
- [ ] PROD-GATE-04 Production release gate (implemented; production readiness requires a green remote gate for the release SHA)
- [x] GEMINI-MODEL-FAILOVER Per-model Gemini quota-aware image-analysis failover
- [x] GEMINI-RPD-RESET Durable Gemini RPD quota-ID recognition and Pacific reset deferral


## Search V3 follow-up

- [x] Durable projection-to-index job handoff and worker Elasticsearch provider registration.
- [x] Versioned V3 search mapping/query foundation with visible text, prefix and fuzzy clauses.
- [ ] Run tenant-scoped V3 backfill and alias activation only after Elasticsearch integration verification in the deployment environment.
- [x] AI Operations Search V3 coverage metrics, explicit audit and repair controls.
- [x] Search V3 metadata text indexing: shared document construction now preserves short OCR terms, safely flattens dynamic metadata, and waits for single-asset Elasticsearch visibility.
- [x] Search V3 repair retries: failed terminal asset_index jobs create one fresh, audit-preserving index job keyed by the failed job ID.

## Test reliability

- [x] TEST-ENV Deterministic backend unittest environment isolation
- [x] CI API suite aligned with durable Search V3 projection-to-index handoff
- [x] WORKER-ELASTICSEARCH-LOOP Persistent worker event loop for sequential asset indexing
- [x] PIPELINE-RECOVERY Durable Google source-sync/download recovery, fresh failed-download repair jobs, and worker-owned async stage execution
- [x] UX File inspector activity timeline and AI Operations header controls
- [x] AI-OPS dashboard response mapping correctness
- [x] AI-OPS responsive filter toolbar refinement

- Pipeline Overview logical-asset aggregation: completed — current dashboard state is deduplicated by active source asset; raw processing attempts are diagnostics only.

- [x] UX Search guide sizing and accessible input-border loading feedback
- [x] UX Search cleared-query loading-state reset
- [x] UX Search-result thumbnail viewport lazy loading with loading skeletons
- [x] UX Search-result grid skeleton while a modern search request is pending


- [x] Viewer folder scopes: tenant administrators can select external Drive
  folders for viewer memberships; Explorer and search enforce the scope.

## Public Review Portal

- [x] PUBLIC-REVIEW-01 Durable share, scope, guest, session, and annotation foundation.
- [x] PUBLIC-REVIEW-02 Server-side public-share principal and fail-closed folder-scope authorization.
- [x] PUBLIC-REVIEW-03 Authenticated share-management API with tenant RBAC, scope validation, rotate/revoke, and secret-free audit.
- [x] PUBLIC-REVIEW-04 Scoped bearer-link read API, durable public rate limits, and CAM-side media delivery.
- [x] PUBLIC-REVIEW-05 Anonymous session guest annotation backend with ownership, rich-text validation, CSRF and durable write limits.
- [x] PUBLIC-REVIEW-06 Public Review frontend portal with bearer-link bootstrap, scoped browse/search/view, annotation interactions, and no mandatory reviewer identity prompt.
- [x] PUBLIC-REVIEW-07 Tiptap rich annotations, safe JSON rendering, and normalized image pins.
- [x] PUBLIC-REVIEW-08 Asset Explorer share-management integration, one-time link handling, rotate/revoke controls, and final Public Review hardening.
- [x] PUBLIC-REVIEW-09 Internal Review Board with tenant-scoped read/stat APIs, resolution workflow, audit, authenticated UI, and production-readiness hardening.

## Cloudflare R2 original-video cache

- [x] Phase 1 private R2 adapter, bounded multipart SHA-256/size verification, tenant-scoped cache metadata, and migration 0082 (local only).
- [x] Phase 2 durable video-only cache-fill jobs, serialized global reservations/quota, LRU eviction, retry, cleanup and reconciliation (local only; disabled by default).
- [x] Phase 3A short-lived signed video URLs and private Worker GET/HEAD foundation (local only).
- [x] Phase 3B Range/206 and Cloudflare edge caching (implemented locally; not deployed).
- [x] Phase 4A persisted global `VIDEO_CDN_DELIVERY_ENABLED` runtime gate and platform-admin UI (local only; default OFF; no playback change).
- [x] Phase 4B Public Review playback integration with exact asset/source authorization, short signed-ticket redirect, session-expiry cap, and provider source fallback (code complete; runtime remains default OFF).
- [x] Phase 4C fail-closed rollout-readiness preflight, signed missing-key Worker probe, and Worker test/typecheck CI coverage (no production deployment; runtime remains OFF).
- [x] Phase 4D tenant-scoped CDN canary gate, HEAD-only READY-object probe, secret-free Wrangler production config renderer, and non-executing Worker rollout planner (no production deployment; runtime remains OFF).
- [x] Phase 4E process-local delivery observability and circuit breaker: decision/fallback/probe metrics, bounded latency samples, admin-safe snapshot, debounced signed HEAD health sampling, and automatic provider fallback without mutating the persisted master toggle.
- [x] Phase 5A production activation hardening: Phase 4E guard is a master-toggle prerequisite, READY preflight is no-redirect, and one read-only activation command combines production/config/DB/quota/canary plus missing-key and READY-object Worker probes. Production runtime remains OFF until an operator explicitly enables it.
- [x] Phase 5B Platform Admin activation console: frontend schema alignment for Phase 4D/4E, rollout/prerequisite visibility, safe process-local redirect/fallback/probe/latency and circuit state, plus rollback access even if observability is unavailable. No production activation.


## Realistic Review UGC

- [x] RRUGC-PINTEREST-AUTO-SCOUT-V2 Persistent tenant Scout Agent pairing, durable campaign leases/scheduling, repeated bounded Pinterest pulls, Pin-level dedupe, automatic analysis/import handoff, login/challenge pause-and-resume, and production UI controls.
- [x] RRUGC-PINTEREST-SCOUT-V40: repair Pin-detail timeout/Pin-link-only HTTP 422 batch failures; bound alt/query lengths; support active older Scouts through auto-agent-only sanitized API requests, without changing strict manual ingestion.
- [ ] Follow-up operational verification: restart Review + Keyword CMD launchers on every Windows Scout machine; confirm both report v42 and no new 422 in server logs.
- [x] RRUGC-STAGE0-FAVORITES-TAILS: persist independent Favorites next to Pick/Used; add favorite-only and server-side Short-tail (2 words), Mid-tail (3–4 words), and Long-tail (5+ words) filters with correct total/pagination; add Alembic 0137 and tenant-scoped API regression tests.
- [x] RRUGC-STAGE0-SEARCH-ASSIST: remove the page-only volume segmented filter; provide tenant-scoped keyword autocomplete/suggestions and account+tenant-isolated browser search history with keyboard navigation, recent removal and clear-history controls.
- [x] RRUGC-SCOUT-V42: reduce Review empty-claim polling during Gemini backpressure; preflight Keyword capacity before Pinterest detail expansion; commit root/deep expansion history only after child processing succeeds so capacity pauses remain retry-safe.
- [x] EXPLORER-CREATE-FOLDER-ALL-USERS: expose Create folder to every authenticated Google Drive user, retain tenant/source/write-scope and Viewer folder-scope enforcement, and refine the Folder+ action icon for context-menu clarity.
- [x] RRUGC-STAGE0-TAIL-KPIS: replace Monthly Volume / High Competition / Zero Volume KPI cards with server-counted Short-tail / Mid-tail / Long-tail cards, reuse word-count icons in filters/actions, and make the master All action clear search, usage, favorite and tail filters.
- [x] AI-OPS-PROVIDER-KEY-POOLS: compact Providers UI; replace fixed 10-slot Image/Video backup pools with dynamic positive slots; add an independently permissioned Inventory backup pool with round-robin resolution and migration 0138.
- [x] RRUGC-STAGE1-COLORWAY-UI: insert a new Stage 1 between keyword analysis and Pinterest discovery; filter source plans by `embroidery_` filename prefix; design the skill selector, 13-color batch strip, progress/status model and responsive workspace; renumber existing user-facing stages to 2/3/4.
- [ ] RRUGC-STAGE2-COLORWAY-RUNNER-RELEASE: implementation now includes the 13-color stock manifest mapping, tenant-scoped durable per-revision/per-color queue, specialized Scale Image Skill, worker, private output endpoint, retries, and existing Stage 2 UI progress (migration 0143). Backend unit/integration-stub tests and Stage 2 feature-state browser QA passed; still awaiting authenticated real-provider end-to-end output verification and explicitly authorized production release. The separate generic visual baseline mismatch remains unaccepted. See `docs/operations/RRUGC_COLORWAY_RUNNER.md`.
- [x] EXPLORER-MOVE-ALL-USERS: allow every authenticated Google Drive user to move files/folders within their authorized source using drag/drop or Ctrl+X/Ctrl+V; preserve Drive write scope, tenant/source boundaries, Viewer folder scope, cycle protection, local source-parent consistency and cache invalidation.

- [x] RRUGC-SIX-STAGE-HARDENING-1: Stage 1 standalone Keyword Skill; Stage 2 Codex executable preflight; Stage 4 scoped cancellation and sequential 3-reference queue; Stage 5 incomplete-only 500-item analysis drain, conservative quality gate and synthetic disclosure. No migration. See docs/operations/RRUGC_STAGE_HARDENING.md.
- [ ] RRUGC-SIX-STAGE-HARDENING-2: Stage 0 keyword provenance and short-token policy, Stage 3 calibrated prefilter and Gemini throughput, Stage 2 output revision history/QA and authenticated canary, Stage 5 gallery pagination.

- [x] CAM-RESEARCH-SYNC-HARDENING: DNS-pinned TLS public RSS collection; no unacknowledged sync, concurrent PostgreSQL ON CONFLICT dedup, complete feed batch ingestion, bounded operator-triggered `research-cycle`. See `docs/operations/CAM_AI_QA_RESEARCH_INTEGRATION.md`.
- [x] RRUGC-MARKET-INTELLIGENCE-PHASE0: canonical Pinterest/Etsy/Amazon listing links accepted into *unverified* read-only Research Inbox without API calls, paid volume requests, migrations or new secrets; domain spoofing rejected, same listing query parameters deduplicated.
- [ ] RRUGC-MARKET-INTELLIGENCE-PHASE1/2: additive marketplace evidence graph + authorized Etsy/Amazon connectors, rate-limit budgets, normalized quote clustering and unified Stage 0 platform filter; access and licensing required before activation. See `docs/architecture/RRUGC_MULTI_PLATFORM_MARKET_INTELLIGENCE.md`.
- [x] RRUGC-STAGE-TAB-COMPACT-PLATFORM: GatorHats Products/Reviews Stage tabs use intrinsic widths rather than 3 full-width sections; active state returns to standard blue underline with no yellow gradient; Stage 0 Platform column is content-sized immediately before Created date. Desktop/tablet/mobile fixture QA passed. No API or database change.
- [x] RRUGC-ALWAYS-ON-DISCOVERY-V1: independent Exa MCP VPS hourly worker with quota/cooldown and SQLite outbox; existing paired-agent tenant-scoped import; read-only Windows Manager v0.1.18 VPS status, best-effort graceful local lease release; CAM Research status/pause controls, responsive UI. Production timer/pairing verification tracked separately; `docs/operations/RRUGC_AUTO_DISCOVER_DEPLOYMENT.md`.
- [ ] RRUGC-ALWAYS-ON-DISCOVERY-V2: dedicated agent credential auto-provisioning via vetted secret channel, verified automatic Stage 0 quote+volume enrichment, cross-platform keyword/evidence association and provider fallback under approved terms.
- [ ] CAM-RESEARCH-SYNC-OPERATIONS: pair dedicated Research Scout Agent with operator-managed secret, verify authenticated production Research Inbox, then enable a non-root reviewed collection/sync schedule. Do not infer authenticated PASS from public-only smoke.

## Branding DNA & Product Creative Studio (2026-10-16)

- [x] BD-PC-SPEC-V1: consolidated approved system design and gated implementation plan (`docs/features/branding-dna/PRODUCT_CREATIVE_SYSTEM_SPEC.md` and `PRODUCT_CREATIVE_IMPLEMENTATION_PLAN.md`). **Documentation only, not an operational release.**
- [ ] BD-01..03: permitted product/gallery evidence → Product/Image Pick & source-verified Best Seller → versioned approved Brand DNA Core and Creative Playbook; never auto-promote inference or equate Pick with sales.
- [ ] PC-00..03: 3 Team Inputs → validated Product Master → editable 10-shot plan → SMS multi-angle mockup for Studio/Detail/Info/Variations **only**, approved Reference Scout for Lifestyle/UGC **only**, separate jobs/QA/final gallery.
- [ ] PC-04..06: Output Pick/Reject with neutral Unreviewed state; controlled supervised rule proposals, offline evaluation, human approval/canary/rollback; tenant permission & multi-device QA gates. See implementation plan for complete work packages.
- [x] SCOUT-MANAGER-EXPLICIT-STARTUP: opt-in `--start-automation` for reviewed Windows restarts, normal three-mode ownership and all-or-paused failure handling; default Idle and marketplace approval gates preserved. Native Windows CI and device installation remain separate release gates; see `apps/scout-manager-desktop/README.md`.
- [x] BD-BROWSER-PLATFORM-REVIEW: public CAM shop pilots need no shop-owner authorization or per-shop approval.json; a shared reviewed marketplace policy (or old exact-shop fallback) governs manually started, bounded browser observations. Etsy written grant remains exact-shop scoped; Amazon Agent Policy and 403/429 challenges remain enforced. Production dispatcher still disabled. Worker 0.2.13 / Manager release 0.1.45; native Windows release and authorized live pilot are separate gates.