# CAM Agent Harness V2 — 0207 production release gate

**Observed:** 2026-11-11 (Asia/Ho_Chi_Minh)
**Scope:** Phase 3C.3B2C2A only; no QA verdicts, model calls, agent promotion, or automatic capture.
**Decision:** **0207 BLOCKED** until independent rollback and backup-restore evidence is complete.

## 1. Completed production stage — compatibility bridge only

- PR #102 CI baseline merged at `165259cc8`; PR #101 0206-compatible bridge merged at `81a8d582bc82747e5a3a1f7914cac93052dd4100`.
- The reviewed backend release script ran on a clean, detached exact-commit worktree:

  ```sh
  bash scripts/cam-rebuild-backend.sh \
    --commit 81a8d582bc82747e5a3a1f7914cac93052dd4100 \
    --no-migrate --keep-releases 2
  ```

- A follow-up validation run ended with **exit code 0**, `100% Deployment completed successfully`, and `Database migrations skipped (--no-migrate); schema compatibility verified`.
- Standard release output reported:
  - `Current: /opt/creative-asset-manager/releases/81a8d582bc82747e5a3a1f7914cac93052dd4100`
  - `Previous: /opt/creative-asset-manager/releases/06656da0100384f4bedee1c31ed2d47fee2428c0`
  - `/var/log/creative-asset-manager/backend-deploy-20261111-105635.log`
- Production `GET /version` returned the exact new commit; `/live`, `/ready` and both PostgreSQL and Elasticsearch availability checks passed. Nine managed systemd units remained active; all four image workers, the video/delivery workers, visual worker and visual encoder passed readiness.
- The two protected releases survived pre- and post-deploy cleanup. One obsolete release was pruned, freeing ~733 MiB; final free disk ~5,551 MiB. This **is not** an isolated restore-capacity proof.
- No 0207 migration was run. Do not enable Stage 1 snapshot capture or trial learning flags in this stage.

## 2. Independent backup/restore preflight — FAIL CLOSED

On 2026-11-11 the existing **read-only** `scripts/cam_pg_g1_preflight_readonly.py` was executed against the production host. Its sanitized output was:

| Evidence | Result |
| --- | --- |
| Local verified backup receipt | PASS |
| Last backup systemd run | 2026-11-10 22:00–22:06 ICT; `Result=success`, exit 0 |
| Remote independent re-download/checksum | NOT_VERIFIED |
| Isolated PostgreSQL restore cluster on this host | NOT_PRESENT |
| Isolated restore rehearsal | NOT_VERIFIED |
| Live PostgreSQL bytes | 4,414,397,463 (~4.11 GiB) |
| Root-volume free bytes | 5,821,235,200 (~5.42 GiB) |
| Estimated extra same-volume restore space | 7,695,338,019 (~7.17 GiB) |
| Minimum free-space reserve policy | 8 GiB |
| Same-volume restore capacity | BLOCKED |
| `g1_recovery` | BLOCKED |
| Production writes performed by preflight | NONE |

**Do not run an isolated restore on the live VPS volume or lower the disk reserve to force a PASS.** Prefer a truly isolated off-host PostgreSQL instance and independent Drive re-download. The receipt and successful systemd unit alone do not verify recoverability.

## 3. Required independent release evidence before 0207

The authorized operator must supply an auditable, sanitized PASS for each gate **without sharing credentials or raw backup contents**:

1. **Rollback marker:** Verify the `current` and `previous` symlinks and their ready/commit markers through an approved scoped host operation. They must point to two distinct, intact releases, with `current=81a8d582...` and an independently runnable `previous=06656da...`. The standard deploy-script output records this but does not replace a separately authorized inspection. Never bypass workspace path controls.
2. **Backup restore:** Independently re-download the latest managed backup, compare the expected checksum/size, pass `pg_restore --list`, restore the **entire** database into a separate, non-production PostgreSQL cluster off-host, and verify schema plus bounded, non-sensitive integrity/row-count checks. Use `scripts/cam_db_restore_rehearsal.py` **only on the isolated off-host runner**, with `--ack-isolated-target` and repeated `--require-table` for every critical public table (at minimum `cam_agent_trial_exposures` and `cam_agent_trial_qa_reviews`; include the exact protected incident recovery table identities identified securely by the operator). A report with `REQUIRED_TABLE_COUNTS_VERIFIED=false` is insufficient for this gate. These optional table checks validate presence and count, not source/restore row-count equivalence; the operator must separately compare sanitized source/restore counts and verify the full restored schema. Document the backup timestamp, checksum proof, isolated instance and result using the approved database-backup runbook. Do not restore into production or print source data.
3. **Resources and liveness:** The off-host environment must have enough capacity; the production host still must pass the reviewed deployment headroom, memory, worker health and release-retention checks. The current same-volume estimate is BLOCKED; it does not certify any hypothetical off-host system.
4. **Feature gates:** Confirm `AGENT_HARNESS_TRIAL_ARTWORK_SNAPSHOT_ENABLED=false` in the effective production configuration and no automatic trial capture/promotion is enabled. Do not expose values of unrelated secrets. Keep `dry_run` as the default for any later authorized API operation.
5. **Code & CI:** PR #100 stays Draft until separate operator review; only merge a commit with full green CI including actual PostgreSQL 12.22/16.4 snapshot migration tests and the 0206 successor check.

## 4. Stage 2 deployment (NOT AUTHORIZED BY CURRENT EVIDENCE)

After all gates in section 3 are verified, use the reviewed separate backend release process to deploy the PR #100 commit **with additive 0207 migration**; do **not** mix it into bridge stage 1 or permit snapshot writes. Confirm the exact Alembic head, zero rows in `cam_agent_trial_artwork_snapshots`, 9/9 service health, and no regression to Stage 1 Output History/Regenerate or Etsy Spy SMS status. The next code-only rollback target is the verified 0206 compatibility bridge, preserving the 0207 PostgreSQL schema and any evidence. A destructive `alembic downgrade` is **not** the rollback strategy.

## 5. Disk cleanup and test-environment hygiene (2026-11-11)

The operator requested cleanup before considering the next production phase
and explicitly disallowed proliferation of VPS test environments.

- Initial audit: root partition 50 GB, ~5.6 GiB free (89% in use).
- Reviewed `cam-safe-disk-cleanup.sh --dry-run` proved exact protected
  `current=81a8d582b` and distinct `previous=06656da01`; the subsequent
  `--apply --vacuum-journal` removed only rebuildable CAM pip cache (479
  files) and archived journals (~302 MiB).
- Deleted **two clean, merged and idle Git worktrees** (CI baseline PR #102,
  0206 bridge PR #101) using non-force `git worktree remove`. Preserved the
  clean PR #100 snapshot worktree and unrelated dirty worktrees.
- Removed only ignored, rebuildable dependencies from an idle historical
  Etsy/SMS checkout (621 MiB Python venv, 142 MiB frontend packages) and
  three other idle old frontend `node_modules` directories (134/134/142 MiB).
  Kept their Git source and any untracked user files. Verified npm cache
  while idle, then cleared its rebuildable content; production runtimes,
  model weights, Codex state, Docker data and backups were untouched.
- Final disk after cleanup: **8.1 GiB free, 83% used**; RAM available
  ~3.9 GiB but the 2-GiB swap was almost full. No temporary PostgreSQL or
  new test worktree was created during cleanup.
- Re-run of read-only `cam_pg_g1_preflight_readonly.py`:
  `backup_local_recent_receipt=PASS`, live DB 4,414,618,647 bytes,
  root free 8,656,510,976 bytes, estimated additional same-volume restore
  7,695,669,795 bytes, reserve 8,589,934,592 bytes;
  `same_volume_restore_capacity=BLOCKED`,
  `isolated_restore_rehearsal=NOT_VERIFIED`, and
  `g1_recovery=BLOCKED`. Thus more free disk does **not** certify 0207.
- Production bridge remains at `81a8d582b`, with API live/ready, 9/9
  managed services active, and successful backup timer. 0207 remains Draft
  and 0207 snapshot capture not deployed. No learning/promotion was activated
  by cleanup; effective feature flags must still be verified separately
  before any 0207 migration or canary.

For future work, reuse existing task worktrees/venvs and remote CI instead of
cloning the whole repository for each testing step. Never turn a dry-run into
an unbounded cleanup or a production DB restore.

## 6. References

- `docs/operations/DATABASE_BACKUP.md` and `docs/plans/DATABASE_BACKUP_IMPLEMENTATION.md` — off-host restore assurance.
- `docs/operations/VPS_DEPLOYMENT.md` — reviewed script, current/previous, readiness, retention.
- `docs/architecture/CAM_AGENT_HARNESS_V2_PHASE3C3B2C2A_IMMUTABLE_SNAPSHOT.md` — 0207 constraints, feature gates and code-only rollback.
- PR #100: https://github.com/BaoNghiaNghia/creative-asset-manager/pull/100
- Verified 0207 CI: https://github.com/BaoNghiaNghia/creative-asset-manager/actions/runs/42955672526
