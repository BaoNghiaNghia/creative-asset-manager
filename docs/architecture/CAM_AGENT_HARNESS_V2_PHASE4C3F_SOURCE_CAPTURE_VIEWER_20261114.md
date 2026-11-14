# CAM Agent Harness V2 — Phase 4C.3F: immutable reader and server source capture

**Date:** 2026-11-14. **Status:** source-only candidate, default OFF. No live HTTP route, Windows worker, migration or production job is touched.

## Superseded by Phase 4C.3G

The original single-Session capture prototype held PostgreSQL row locks during
the HTTPS request. It has been **retired and made fail-closed**. Only the
default-OFF three-stage Session-factory path in
`docs/architecture/CAM_AGENT_HARNESS_V2_PHASE4C3G_STAGED_SOURCE_CAPTURE_20261114.md`
may be used for further development. The text below records historical
Phase 4C.3F design and MUST NOT be used as permission to run the former API.

## Why this phase

Phases 4C.3C–E provide fenced slots, receipt prepare/accept and read-only reconciliation. Two important parts were absent: a server-owned producer for the previously defined signed source snapshot, and an authenticated read path for immutable PNG receipts. Live `/image-agents/{agent_id}/result` and `/image-agents/assets/{job_id}` still use the legacy `<job_id>.png` storage and must not be switched without an independent cutover.

## Implementation

**Descriptor-pinned immutable storage** (`fenced_storage.py`):

- Only two hardcoded namespaces are permitted: `fenced-source-v1` with blob key equal to its SHA256 and `fenced-v1` with receipt-id key distinct from output SHA256.
- No caller path is accepted. The trusted absolute root and every child directory are opened using `O_DIRECTORY | O_NOFOLLOW` and `dir_fd` traversal, refusing symlink hops; the final file is opened `O_NOFOLLOW` and checked as a regular file. The **same descriptor** is read, bounded to <= 22 MB and checked against exact digest and byte count before returning bytes.
- Source writer creates permission-restricted tempfile with exclusive flags, writes+fsyncs bytes, hard-links without overwrite and fsyncs each new parent directory entry and final shard. Existing final names must match the original bytes. No destructive cleanup of abandoned finalized blobs.
- This replaces the path-followable source verifier read with pinned-byte verification and provides a future preview read library. A configured root must still have trusted non-writable ancestor directories.

**Review-only immutable viewer adapter** (`fenced_viewer.py`):

- Default OFF, PostgreSQL-only; caller MUST authenticate the current user and supply its actual authorized `tenant_id`.
- Read-locks the tenant-scoped ProcessingJob and accepted receipt, checks result status and review-only metadata, then reads pinned, verified PNG bytes. Returns bytes, never a mutable FileResponse path or commercial rights/print-ready approval.
- It cannot serve legacy PNGs, change statuses, create jobs or make an unverified receipt viewable. No route imports this adapter yet.

**Bounded independent server source producer** (`fenced_source_capture.py`):

- Default OFF and emergency-stopped. An existing authorized slot and exact fencing token are mandatory; no job claim is created.
- Independently derives the source URL from the server-owned job payload and validates its pre-recorded URL SHA256. Only exact `i.etsystatic.com`/`img.etsystatic.com` HTTPS URLs with restricted ASCII paths are accepted.
- Resolves DNS, rejects ALL private/non-global answers, **pins the selected public IP for the connection**, performs standard-CA TLS certificate validation with original hostname/SNI, uses direct sockets rather than environment proxies, sends GET with no redirects and no content-encoding, enforces positive image/jpeg/png/webp content type, Content-Length, <= 22 MB and a bounded read deadline. PNG/JPEG/WEBP pixels are decoded and checked against a 50M-pixel ceiling.
- Rechecks job+slot after fetch under caller-owned SQL locks. Stores downloaded bytes as content-hashed immutable source blob, then creates a server-only 256-bit HMAC manifest bound to tenant/job/device/claim/start/URL digest/source digest/source bytes and capture timestamp. Caller owns commit; crash after blob fsync but before commit can leave a safe orphan that a retry may reuse. Different source or stale epoch always fails closed, no silent rewrite.
- **Important production blocker:** this narrow prototype holds PostgreSQL row locks while performing bounded HTTPS I/O. Before any live route, split it into a separate I/O stage and short authoritative claim revalidation transaction, with explicit network concurrency, retry budget, cancellation and proxy/egress policy. No source request is made by any live CAM component.
- A verified source snapshot is NOT browser/provider evidence. The existing server proof verifier still returns `may_accept_output=false` even on success; no automatic output approval occurs.

## Tests and release gates

- Unit tests for source fetch SSRF boundaries, mixed public/private DNS, TLS SNI/host vs numeric pinned IP, redirects, content-encoding, fake/malformed images and size limits. All network tests use fake TLS/sockets: **zero actual Etsy requests**.
- Tests for no-follow symlink root/namespace/shard/file, corrupted/duplicate content, distinct receipt hash vs key, safe immutable reads and metadata bounds.
- PostgreSQL 12/16 tests for exact existing claim capture, source manifest verification, idempotent retry, new epoch conflict, crash after fsync/SQL rollback and read-only tenant-scoped preview with corrupted file rejection.
- CI, even if successful, is NOT an off-host restore of the real production PostgreSQL backup. Migrations 0209/0210, result route, Windows agents, automatic dispatch, provider calls, training/learning and feature toggles remain **OFF**. Preserve production current + rollback.

## Next gates

1. Independently verified off-host restore of a real production backup, complete schema/row counts/checksum, audited rollback.
2. Refactor source capture into two-stage asynchronous transaction-safe bounded I/O, independent browser/provider output attestation and mandatory immutable source-to-output lineage.
3. Review authenticated endpoint and media viewer cutover, prevent any dual-writer window between legacy and new receipts; implement human-only reconciliation UI.
4. Run a single controlled job canary only after all prerequisites; no opt-in activation by this code alone.
