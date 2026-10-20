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

## Tests and invariants

- Unit tests: only CAM supplies shop URLs; inactive/unapproved/malformed or
  cross-origin shops fail closed before browser launch.
- Pairing token never printed; preflight prints only aggregate counts.
- No default approval; independent exact-shop dated approval remains required.
- Pilot max 5 product pages; no CAPTCHA bypass, proxy rotation or third-party
  scraping API.
- Existing Review, Keyword and CapStack workers remain untouched.
