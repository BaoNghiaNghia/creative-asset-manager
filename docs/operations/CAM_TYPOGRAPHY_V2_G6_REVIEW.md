# CAM Typography Engine V2 — G6.2 Private ImageGen Human Review

Date: 2026-10-29. Implementation-only, feature OFF; no production migration or deployment.

- `typography_imagegen_review.py` checks existing private per-tenant batch and slot only; it will never generate an image, create a missing batch, upload assets, change Stage 1 history or Stage 2, or promote a V2 skill to V1.
- The operator must inspect the real immutable PNG first. A review decision compares its SHA-256 to the exact inspected image and its G6.1 immutable manifest; reruns G5 PNG QA and verifies the latest source plan, tenant canary, rights, approved Style DNA and Stage 0 keyword via the caller. Any mismatch fails closed.
- Accepting requires separate explicit spelling, originality and embroidery confirmations. Rejection requires a bounded reason code. A decision is saved with no-overwrite semantics to `review_XX.json`, preserving both the original PNG and `slot_XX.json`.
- The review JSON always sets `eligible_for_publication=false` and `stage1_output_saved=false`; it is an internal human checkpoint, **not authorization to publish**. There is no web route, no scheduled worker and no durable DB job integration at G6.2.
- The operator-only CLI `app.modules.realistic_review_ugc.typography_imagegen_review_once` supports `inspect`, `accepted`, `rejected`. Without `--execute` it accesses no DB/provider/staging. With `--execute` it requires the same explicit gated canary as G6.1, and a server-resolved user/reference/keyword/plan. It does not grant RBAC based on a supplied CLI user-id: use only a trusted, authorized production operator, not an end-user endpoint.
- Example shape (use real authorized IDs only after G0.1 backup/restore):

```bash
PYTHONPATH=apps/api apps/api/.venv/bin/python -m app.modules.realistic_review_ugc.typography_imagegen_review_once \\
  --tenant-id TENANT --user-id REVIEWER --keyword-id KEYWORD_UUID \\
  --reference-id REFERENCE_UUID --batch-id BATCH_UUID4 --slot 1
```

An approved output is still private. Later G6.3 must introduce durable database-backed checkpoints, authenticated RBAC review UI/API, asset upload authorization, Stage 1 partial export and Stage 2 handoff with rollback and tenant tests before any production V2 image publishing.

## Evidence

- 2026-10-29: 35/35 focused G6.1+G6.2 Python tests passed; no provider call and no production effect.
- Production G0.1 still blocked: full off-host PostgreSQL backup/restore, 0188/0189 compatibility, rollback and resource headroom.
