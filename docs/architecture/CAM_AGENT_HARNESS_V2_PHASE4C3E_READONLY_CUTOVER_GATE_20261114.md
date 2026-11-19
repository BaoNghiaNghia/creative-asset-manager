# CAM Agent Harness V2 — Phase 4C.3E: read-only receipt reconciliation and source gate

**Date:** 2026-11-14. **Status:** code-only, default OFF, no production migration, no routes or workers wired.

## Findings from the actual Image Agent source

The active `/image-agents/{agent_id}/result` route checks the paired token and
the legacy lease, receives `png_base64` and a caller-reported
`source_sha256`, writes the PNG under a mutable `<job_id>.png` filename,
and then commits ProcessingJob completion. A client-reported source hash and
orchestrator QA flags are not independent evidence that the server fetched the
original Etsy image, that a browser truly executed a prompt, or that a provider
created the claimed output. The original viewer still reads `<job_id>.png`.
This PR does **not** change that route, storage policy or the Windows agent.

## Implemented in Phase 4C.3E

1. `fenced_reconciliation.py` is a default-OFF **read-only** PostgreSQL-only
   inspector. It loads a tenant-scoped ProcessingJob, 0210 receipt, 0209 slot
   and paired agent, then classifies:
   - `receipt_not_found`: no 0210 receipt for the job;
   - `prepared_awaiting_blob`: active claim/slot and no result on disk;
   - `prepared_blob_recoverable`: active claim/slot and the exact immutable
     result bytes are already on disk; future retry must still revalidate and
     commit using the **mutating** fenced service under locks;
   - `prepared_stale_epoch`: wrong, expired, cancelled, superseded or revoked
     source/slot/device; **manual reconciliation required**, never promote;
   - `accepted_verified`: DB receipt+job metadata and on-disk digest match;
   - `accepted_blob_missing`, `accepted_blob_corrupt`,
     `accepted_metadata_mismatch`, `receipt_integrity_mismatch` and other
     storage or ledger errors: **manual intervention required**.
   No state transition, file deletion, retry, dispatched task, external call
   or approval is performed. The inspector result is a potentially stale
   snapshot, not authority for a write. The future viewer must use a pinned
   no-follow file descriptor, not trust a path returned by diagnostic code.

2. `fenced_source_provenance.py` verifies only **independent source bytes**:
   an exact tenant/job/agent/attempt/claim-bound HMAC server record, an allowed
   Etsy CDN URL digest, a short capture window and a locally stored immutable
   `fenced-source-v1/<digest-prefix>/<sha>.bin` file. The signature is domain
   separated and checked in constant time. Missing or tampered record, old
   claim, different origin/digest, revoked job, expired capture, missing storage
   or symlink path fails closed.
   - **No producer/issuer is provided**: CAM has no independently verified
     server-side source snapshot today. Current jobs therefore cannot pass
     this gate.
   - Even successful `source_capture_verified` sets
     `browser_provenance_verified=false`,
     `provider_provenance_verified=false`, `may_accept_output=false`.
     This verifier cannot be used to pass the external execution gate by itself.
   - This module does not create HMAC keys; a future issuer must use a
     server-only independent 256-bit key, capture bytes from an allowlisted
     URL without following unsafe redirects, verify and persist the immutable
     snapshot, then sign only the final authoritative digest.

3. Test matrix adds immutable receipt classification, stale/revoked/expired
   sources, source signature forgery, wrong tenant/attempt/origin, symlinked and
   corrupted source/result blobs, and PostgreSQL read-only persistence checks.
   Existing 0209/0210 and 4C.3D crash/retry tests remain in the CI matrix.
   The small duplicate directory fsync in the existing module is removed.

## Incomplete critical gates before any production activation

- **Migration gate:** off-host restore of a *real* production backup, integrity
  checks and audited rollback for 0207+ not yet verified. Do not migrate 0209
  or 0210 on production, regardless of synthetic CI success.
- **Source capture producer:** server-owned HTTPS capture with hardened SSRF
  defenses, explicit byte limit, content-addressed storage and signed
  manifest; no AI provider or browser fetch implied by this source gate.
- **Browser/provider provenance:** independently verify execution/journal and
  exact output lineage. A self-reported `qa`, signed device heartbeat, or
  source snapshot alone must **not** satisfy this.
- **Transactional cutover:** new endpoint mode must require current fencing,
  server capture, provider evidence and receipt consistency under one reviewed
  protocol. No dual-write window between legacy `<job_id>.png` and immutable
  receipt storage; map authenticated read APIs to 0210 and verify physical
  bytes before handing off, with safe no-follow file access.
- **Recovery operations:** provide a human-reviewed tenant-scoped recovery
  UX for stale prepared receipts and missing accepted blobs; no automatic
  promotion or garbage collection based only on an expired timestamp.
- **Canary:** explicit manual 1-job controlled canary only after the preceding
  gates and zero unauthorized background dispatch. Keep current + rollback.

**No production change:** this phase deliberately does not call model APIs,
browse Etsy, update the extension, commit a result, claim a job, or issue a
new credential. Feature gates default OFF.
