# Branding DNA — offline signed preflight envelope (BD-SCAN-CALLBACKS-02B.3a)

Implemented as a **non-executable library only**, not a route or a live
scanner. API signer `apps/api/app/modules/branding_dna/signed_job_envelope.py`
uses Ed25519 with an explicitly injected private key. Windows worker verifier
`apps/branding-dna-worker/src/signed_job_envelope.mjs` accepts only a
trusted, separately pinned public-key registry and independently obtained
expected tenant, shop, task, snapshot, agent, lease, generation, platform and
scope digest. There are **no embedded or generated production keys**.

The envelope's `purpose` must equal `preflight_only` and
`scan_enabled` must be `false`; it never grants a marketplace right,
returns a runnable URL, obtains a task lease, ingests media or permits browsing.
The existing lease API remains `job:null, scan_enabled:false`.

## Signed bytes and bounds

The signature is Ed25519 over UTF-8/ASCII bytes consisting of the exact header
`branding-dna-preflight-v1\n`, then 18 ordered `key=value\n` lines:

`agent_id, expires_at, issued_at, key_id, lease_generation, lease_id,
max_gallery_items, max_pages, nonce, platform, purpose, scan_enabled,
scope_sha256, shop_id, snapshot_id, task_id, tenant_id, version`.

All UUIDs must be canonical lowercase; the nonce is a UUID; scope hash is
lowercase SHA-256 hex. Keys are bounded ASCII names. Schema accepts only
Amazon/Etsy, up to 5 pages / 20 gallery entries, a positive generation at
most 1,000,000 and a max 5-minute signature lifetime. Future issuance tolerance
is at most 30 seconds. The exact response contains the 18 fields plus one
unpadded base64url 64-byte Ed25519 `signature` field. Signatures fail if any
field is added, modified, dropped, expired, mismatched or issued by an
unrecognized key. Nothing uses a worker's bearer token as a signing key.

## Verification

- Python tests validate all bounds and use an **ephemeral test-only** key
  to cross-verify the signature in the actual Node.js verifier.
- Node.js tests reject swapped shop/worker, invalid scope, tampering, wrong
  public key, out-of-window timestamps, executable intent, unknown key IDs
  and extra fields.
- No new migration revision, API router changes, shared worker imports, UI
  changes or production deploy. The isolated integration gate was repaired to
  scope protected downgrade opt-ins to a temporary `cam_integration` database
  only; the older migration 0138 was repaired to bind and escape its SQL LIKE
  pattern for psycopg. No protected production downgrade is permitted.
- Integration regression completed upgrade/downgrade/re-upgrade and 51 actual
  PostgreSQL/Elasticsearch/pipeline tests without touching production state.

## Remaining prerequisites

Before a separately reviewed executable v2 protocol: obtain and independently
verify actual platform-specific authorization; establish operator-controlled
private key rotation and pinned worker public-key distribution; connect a
lease issuance transaction to a trusted authorization record; ensure revocation,
nonce replay protection, scoped URL allowlisting, redirect/robots policy,
source provenance and rights checks; complete Windows browser QA, CI, backup /
restore rehearsal, and approved production rollout. **Do not treat this
preflight signature as platform permission.**

Rollback: removing these unused libraries/tests does not affect database,
existing agents, active queues or production state.
