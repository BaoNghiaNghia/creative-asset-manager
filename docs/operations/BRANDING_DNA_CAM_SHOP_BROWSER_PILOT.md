# Branding DNA — CAM Shop Library → Windows browser pilot (controlled rollout)

## Outcome

The browser pilot now uses the **CAM Shop Library source of truth**. A local
browser run chooses a shop by CAM `shop_id`, not a second list of marketplace
URLs. The agent's existing RRUGC pairing credentials retrieve the tenant's
configured shops from `GET /api/v1/branding-dna/scout-agents/{agent_id}/shops`.

This is an **on-demand, single-shop, bounded local pilot** and is NOT yet an
unattended queue worker. The production dispatcher is deliberately disabled
until independent source permission and a verified, fenced task-execution
protocol are deployed. No job is automatically claimed, no marketplace API
or third-party scraping service is used, no evidence is committed to CAM.

## Working commands on DESKTOP-3FR0LF0

Workspace: `D:\Desify_Agentic\scan_pinterest` — never use the old
`BaoNghia` machine and never overwrite any existing Pinterest Chrome profiles.

From a Windows terminal in the Scout repository:

```powershell
cd D:\Desify_Agentic\scan_pinterest
node apps/branding-dna-worker/src/cam_shop_pilot.mjs `
  --preflight `
  --env-file D:\Desify_Agentic\scan_pinterest\scout.local.env
```

The read-only preflight fetches the existing shop registry and prints only
aggregated counts (platform, inactive, relationship/access gaps, invalid
shop URL). No marketplace browser is opened and **0** jobs are generated.

For an **individually authorized** exact shop (not a competitor by default),
after reviewing its marketplace terms, robots policy, source rights and
platform-specific permissions, an operator may prepare a local approval
record and run **one** browser pilot:

```powershell
node apps/branding-dna-worker/src/cam_shop_pilot.mjs `
  --shop-id "<UUID from CAM Shop Library>" `
  --env-file D:\Desify_Agentic\scan_pinterest\scout.local.env `
  --approval-file "D:\BrandingDNAAuthorizations\approved-shop.json" `
  --profile-dir "D:\BrandingDNABrowserProfiles\shop-pilot-1" `
  --report "D:\BrandingDNAPilotReports\pilot-1.json"
```

**Do not create a fake approval file or mark a shop owned/authorized without
real authorization.** The approval must match the exact shop URL and platform,
be current (less than 30 days), record a terms/robots/rights review, and
contain Etsy's express written-authorization reference or Amazon's agent
policy review. The CAM registry status must be `active`, its access mode
`owned` or `authorized`, and the pairing must remain active.

By default the pilot uses visible browser link clicks, 10–18 second bounded
navigation cooldown and up to 3 listing pages, in a dedicated Chromium
profile. It stops on access denial, login/CAPTCHA challenges, unexpected
redirects, missing title or invalid product links. It records
`partial_unverified` observations with SHA-256 URL fingerprints, not
images or claimed sales, and writes a local report only. No automatic
download/upload occurs.

## Curated shop lifecycle — no silent activation

New CAM operator endpoint:
`POST /api/v1/branding-dna/shops/{shop_id}/lifecycle`
with operator permission `ai_provider.configure`.
For **active**, the JSON payload must contain
`{"status":"active","declaration_id":"<latest approved declaration UUID>","acknowledge_marketplace_rights_not_verified":true}`.
The latest declaration must document an `owned` or `authorized`
relationship, with an independently recorded `documentation_noted`
review. Old, unreviewed, rejected and competitor-only declarations are
rejected. The endpoint changes the catalog's `status/access_mode` only:
`rights_verified=false`, `scan_enabled=false`,
`dispatchable=false`, `jobs_created=0`. It never modifies any queue.

An active shop can be paused using
`{"status":"paused","acknowledge_marketplace_rights_not_verified":true}`
without the paperwork required for activation. There is no bulk activation.
The separately recorded marketplace terms and exact-shop authorization
still gate the local Browser Pilot. A shop being `active` is NOT permission
to crawl or proof that an Amazon/Etsy adapter can run unattended.

## Current production blockers

Production audit on 2026-10-20 observed **33 shops** (1 Amazon, 32 Etsy),
**33 inactive**, **33 undeclared source access**, **0 staged/pending/leased
jobs**, and a connected Scout Manager/Branding DNA standby agent. These
are not problems solved by merely switching an agent from standby to RUN.

Until the source rights and shop activation are actually verified, **do
not auto-scan all 33**. The next production work involves: authenticated
per-shop permission evidence with reviewer segregation; a job-lease API
bound to verified marketplace adapter and shop; task completion with
independent evidence verification; replay-safe report ingestion; and
completion/retry telemetry. Browser observations alone must not be promoted
to verified Brand DNA data.

## Quarantined browser observations in CAM (Phase 2)

For a shop independently activated through the reviewed catalog lifecycle,
an operator can import a **completed, bounded browser pilot report** to CAM.
The new endpoints require `ai_provider.configure` to write and
`ai_operations.read` to list/review:

- `POST /api/v1/branding-dna/shops/{shop_id}/browser-observations`
- `GET /api/v1/branding-dna/shops/{shop_id}/browser-observations`
- `GET /api/v1/branding-dna/shops/{shop_id}/browser-observations/{report_id}`

Only `preview_completed` with 1–5 fully parsed listing observations is
accepted. Every observation is restricted to a title, listing key, SHA-256
page/image URL digests and explicitly unknown rights/sales status.
No image media, raw source URLs, screenshots, session cookies or arbitrary
browser-output JSON are persisted. The shop must be active and
`owned`/`authorized`, and its canonical URL hash must match.
Draft/unreviewed shops are rejected before any database write.

The API deduplicates using a per-shop `report_key`, returns 200 for exact
replay and 409 for conflicting content under an existing key. All records
remain `browser_observation_unverified` and cannot be used as evidence of
marketplace authorization, verified product photos or sales statistics.
There is no job dispatch, automatic marketplace browsing or verified
Brand DNA promotion in these endpoints.

On Windows, **only after** a manually authorized single-shop pilot,
passing `--prepare-import` alongside `--report <path>` saves a second,
strict `<path>.cam-import.json` file. It intentionally does **not**
transmit that file using the Scout token. Import requires a separate
authenticated CAM operator with `ai_provider.configure`; the paired
Scout's browser heartbeat token is not an operator write credential.
The JSON may be submitted to the corresponding operator API after manual
review. Partial errors and blocked/CAPTCHA pilots are refused as import
payloads; their original local report remains available for investigation.
The import is append-only (no delete or automatic downgrade).

**Current rollout:** no automatic import or unattended scanning of 33
inactive/undeclared shops. Browser pilot metadata can now enter the audit
pipeline once an exact shop has passed the required review.

## Tests and invariants

- Unit tests: only CAM supplies shop URLs; inactive/unapproved/malformed or
  cross-origin shops fail closed before browser launch.
- Pairing token never printed; preflight prints only aggregate counts.
- No default approval; independent exact-shop dated approval remains required.
- Pilot max 5 product pages; no CAPTCHA bypass, proxy rotation or third-party
  scraping API.
- Existing Review, Keyword and CapStack workers remain untouched.