# Branding DNA — Amazon/Etsy Browser Agent Pilot

## Scout Manager v0.1.32 — Native Rust config compatibility

Rust `PilotShopConfig` now explicitly recognizes the Node runner's optional
`interaction_mode` and `maximum_delay_ms` fields while retaining
`deny_unknown_fields`. Existing Windows configs with `visible_click` no
longer fail native deserialization. Round-trip saving preserves those keys,
the local browser profile, checkpoint paths and authorization-file path.
Frontend shows the real reason for config read failures. No migrations,
remote sync writes, credential changes or marketplace scans are added.

## Scout Manager v0.1.31 — CAM Shop Library read-only sync

The Windows browser panel now distinguishes CAM tenant Shop Library and
local saved-shop library. The Tauri command authenticates with existing
RRUGC Scout pairing credentials and calls
`GET /api/v1/branding-dna/scout-agents/{id}/shops`. Backend validates
the active Scout token and returns only the caller tenant's Amazon/Etsy
shop metadata with `scan_enabled:false`. The command never returns tokens,
does not import approval decisions, and never dispatches browser activity.
An operator selection saves only the local scoped shop configuration
and reuses the local profile if it already exists.
Unpaired machines receive a clear error and can still use local shops.
No product, image, screenshot or Chromium profile is stored on the VPS.

## v0.2.3 — Traffic pacing, not stealth automation

Windows visual pilot uses bounded navigation cooldowns before opening the
shop, each actual product click and return-to-shop. Defaults are
`minimum_delay_ms=10000` and `maximum_delay_ms=18000` for new
Scout Manager shops; CLI config may pin explicit values. Old configs without
a maximum are assigned `minimum + 8000` (up to 60 seconds).
Sampling only spreads requests to reduce bursts and server load: it must
not mimic human keystrokes/cursor patterns, alter Agent UA, evade throttling
or bypass challenges. 403/429 and blocking pages stop for operator review.
No external marketplace pilot is permitted without separate shop approval.

## v0.2.2 — Local SQLite product checkpoint after every manual pilot

Every permitted manual run reconciles earlier immutable JSON reports into
a per-shop SQLite ledger under `<report_dir>/.bdna-local-progress/`. The
browser receives a bounded list of previously successful product keys and
skips them during visible-link discovery. Reports are written first and
recorded second; after a crash between these steps the next **manual**
launch restores state from the locally saved, shop-hash-bound report. The
ledger uses WAL and durable SQLite commits, handles cross-shop isolation
even when reports share a folder, and rejects symlinks/tampered reports.

The count means **locally observed products**, not verified rights,
sales/bestsellers or full-shop coverage. Failed details/CAPTCHA do not count
as successfully observed. On the initial visible shop page, if no new links
remain the pilot ends in `needs_operator` for further review; it does not
paginate or scrape additional pages automatically. No auto-run, VPS media,
CAM evidence import, cloud storage or hidden headless human impersonation.

## v0.2.1 — Windows visible UI pilot and VPS release size

The pilot supports `visible_click` via `getByRole("link")`, UI
`click()`, and validated `goBack()` to the shop for subsequent products.
Existing Scout Manager shops use this mode whenever **Show Chromium** is on;
a hidden-headless legacy run continues using direct bounded navigation.
No attempt is made to impersonate human timings or bypass Agent Policy.
Browser sessions and approvals are local to Windows; only the Git source,
not the Windows browser runtime, may exist in backend development checkouts.
The production backend release script excludes
`apps/scout-manager-desktop`, `apps/branding-dna-worker`, and
`releases/branding-dna` from every *new* immutable backend release.

Do not infer Windows deployment from this source change. The Windows
Scout device is offline until the user starts it; browser pilot remains
operator-approved and local. Production scanner lease remains disabled.
Existing backend releases still require a separate audited cleanup after
verifying no service uses those directory trees.



Status: **local pilot code available, production dispatch disabled**, 2026-10-16.

## Architecture and scope

CAM Shop Library → operator reviews a precise storefront → dedicated Windows
Chromium agent navigates visible shop/product pages → produces **local**
metadata-only, partial/unverified report → operator review. The report is NOT
uploaded, attached to Brand DNA or used to rank Best Sellers yet.

The agent **does not** call Etsy Open API, Amazon SP-API, an external scraping
service, or private seller dashboards. CAM API is used separately for
management/heartbeats; it is not a marketplace API. Ordinary storefront
JavaScript may make its own requests, as any browser does.

### Provider policy gates (do not skip)

- **Etsy:** Etsy policy says automated access/scraping needs express written
  permission. Require an actual written Etsy authorization and a reference
  before enabling an Etsy pilot. Registering a shop or owning it is *not* the
  same as Etsy's written permission.
- **Amazon:** Check the marketplace-specific Agent Policy and conditions. The
  pilot announces itself with the User-Agent `Agent/CreativeAssetManagerBrandDNA`
  and does not mimic human navigation patterns, evade rate limits, rotate
  proxies, bypass CAPTCHA or hide its identity. Stop if the service denies
  or restricts agent access.
- Platform access and media/AI rights remain independent. A public image does
  not automatically convey rights to re-use it or train a model.
- Required: operator-approved exact shop, current policy/robots review and
  source-rights review. UI cannot override these restrictions.

Official policy references (reconfirm at execution time):

- https://www.etsy.com/legal/api/
- https://developers.etsy.com/
- https://sellercentral.amazon.com/help/hub/reference/external/GS83KH2MA7HM69PH

## Windows release location and verified installer

The Scout Manager v0.1.28 Windows CI now publishes a **versioned GitHub Release**
with portable and NSIS executables plus SHA256 list after all tests/build
pass. On the authorized Windows machine, run
`UPDATE_SCOUT_MANAGER_RELEASE.cmd` from the existing
`D:\Bot_Tool_Auto_Game\scan_pinterest` checkout. The download script is
`scripts/install_scout_manager_release.ps1`, and it saves release files in
`scout-manager-releases` with per-file SHA-256 checking before any copy.
It preserves all previous versions, profiles, reports and tokens.
Windows hosts that are offline are not silently modified.

## Local handoff and independent rights-verification gate (v0.1.28)

The **Prepare handoff** action collects only active-shop, exact-hash Pick
decisions from completed pilot reports. It emits a bounded local-only JSON
candidate packet, not a CAM import payload, in the user's
`%LOCALAPPDATA%\CreativeAssetManager\BrandingDNA\handoffs` folder.
Each candidate must still be validated through official seller evidence,
marketplace authorization, explicit image/artwork usage rights, and original
media provenance. All of these checks are **unset**, and the packet
explicitly denies auto-ingestion and training eligibility. No API request,
marketplace scan, production write, job dispatch or approval is triggered.

CAM's existing `/shops/{shop_id}/listings/import` endpoint deliberately
accepts complete authorized metadata exports only; the local browser
handoff is **not** compatible with that endpoint. A separate, tenant-scoped
rights-verification workflow and source-revision reconciliation is required
before integration.

## Per-product operator shortlist (Scout Manager v0.1.28)

The local pilot result panel now supports Pick, Reject and Reset decisions
with bounded notes per observed product. Decisions are persisted *outside
the git checkout* under the current Windows user's
`%LOCALAPPDATA%\CreativeAssetManager\BrandingDNA\reviews`.
Each review is tied to the shop URL hash, exact immutable pilot report
SHA-256, report filename and listing key/index. Switching shops cannot
load another shop's decisions; replacing the underlying report invalidates
its previous shortlist. Invalid or incomplete product observations cannot
be shortlisted, and blocked pilots cannot create positive picks.

**Pick is not approval.** This stage never claims seller rights, permits
training, stages CAM scan tasks, sends HTTP to CAM, or changes
`evidence_committed` from false. A separate tenant-scoped authorized
review/ingest flow with validated metadata/gallery rights and audit logs
is required before any CAM database records are created.

## Multi-shop library (Scout Manager v0.1.28)

Operators may save up to fifty Amazon/Etsy storefront pilot configurations and
switch using **Saved shop library** in the same Scout Manager window. Per-shop
Chromium profiles, reports and operator-maintained authorization files remain
isolated and are not reset when changing shops. The active shop config is
also retained in `config/pilot.local.json`; the per-shop library lives at
`%LOCALAPPDATA%\CreativeAssetManager\BrandingDNA\shop-library`.
When a prior legacy config is encountered it is added to the library on the
next shop switch/save; it is not silently edited on application launch.

No shop is scanned automatically, and switching does not bypass the runtime's
per-shop approval validation or start the browser pilot. Shop library entries
are **not CAM database shops** and cannot be ingested, approved or dispatched
without a separately developed, tenant-scoped manual-review workflow.

## Inspect local DOM pilot evidence (Scout Manager v0.1.28)

The integrated Manager shows the eight most recent **read-only local**
pilot results for the selected shop. Data is accepted only from a bounded
JSON report whose normalized shop SHA-256, platform, scan_enabled=false
and evidence_committed=false match the active local configuration.
Product titles/keys, image-reference *counts*, observed time and blocker
reasons are displayed; raw image URLs and approval credentials are not
exposed. Results for another shop cannot appear in this view. New shops
get separate local report folders. Existing report paths are retained.
This is **not** a source-of-truth Brand DNA result or a production upload.

## GUI-based local shop setup (Scout Manager v0.1.28)

In the Branding DNA card, choose Amazon or Etsy, paste the exact storefront
URL, set the preview size (1–5 product pages) and choose whether Chromium is
visible. Save creates **only** a local per-shop configuration: approval,
profiles and reports live under the Windows user's LOCALAPPDATA outside of
the source checkout. Existing paths for an already configured shop are
preserved, and changing shop creates a new profile and approval path.
The Edit approval action creates a **default-deny** file if one is absent.
The operator must have genuine platform authorization/rights and manually
document it; presence of a JSON file **does not imply permission**.
The browser worker verifies the required approval fields at run time.

The "Run automation" global control continues to affect Pinterest scouts only;
a deliberate click on Run pilot is still required. No live marketplace
scanning has been verified from the user's Windows PC.

## Integrated Scout Manager mode (2026-10-16)

Branding DNA Browser Agent is available in the **existing Pinterest Scout
Manager** (`apps/scout-manager-desktop` v0.1.28) from the same
`D:\Bot_Tool_Auto_Game\scan_pinterest` checkout. Buttons: Install runtime,
Edit shop config, Check, Run pilot, Stop, and Live logs. This does not change
the three Pinterest modes' global Run automation behavior. Branding DNA
remains **manual and local only**, without auto-restart or production result
upload. Its Chromium profile, reports and logs are isolated.

The Scout Manager Windows CI workflow builds the installer and portable EXE.
Existing `scout.local.env` and Pinterest profiles must be retained during
upgrades. A real Windows Chromium integration test and authorized live
storefront pilot are still pending.

## Windows Portable release v0.2.0

The repository now publishes a deterministic Windows bootstrap archive:
`releases/branding-dna/BrandingDNA-BrowserAgent-v0.2.0-win-x64.zip`.
This is **not a bundled native EXE**. It contains the source, pinned lockfile,
CMD launchers, default-deny example configs, QA tests and this guide.
Install official Node.js 22.13+ first, then run `INSTALL_TOOL.cmd` to
download Playwright's Chromium to the dedicated local app-data directory.
`CHECK_TOOL.cmd` is an offline readiness check; `START_BROWSER_PILOT.cmd`
runs only an expressly approved shop from `config/pilot.local.json`.
The installer preserves an existing `pilot.local.json`, and user credentials,
cookie profiles, real approval files and reports must remain outside the ZIP
and repo. `QUICK_START_VI.txt` has concise Vietnamese setup instructions.

Linux release QA has exercised `npm ci` and all Node unit tests from a
freshly extracted ZIP. **Windows CMD/Chromium and real storefront operation
have not been verified on the target Windows machine yet.**

## Worker package

`apps/branding-dna-worker/` is the only browser pilot runtime. It uses pinned
Playwright 1.56.1 and Node.js 22.13+. Keep its Chromium and data directory
separate from CAM VPS, Pinterest Scout, normal Chrome/Edge profiles and other
desktop automation tools.

On a dedicated Windows host after runtime and policy approval:

```cmd
cd D:\CAM\branding-dna-worker
npm ci
npx playwright install chromium
npm test
```

**Do not use the production heartbeat command to start the browser.**
`npm start` remains heartbeat only. Local browser pilot is a separate,
manual command that refuses to run without an approval file.

Store a local approval file **outside Git**, supplied and reviewed by the
operator for the exact storefront. Schema for an unapproved draft:

```json
{
  "platform": "amazon",
  "shop_url": "https://www.amazon.com/stores/ExampleCaps/page/EXAMPLE",
  "browser_access_approved": false,
  "source_rights_reviewed": false,
  "platform_terms_reviewed": false,
  "robots_policy_reviewed": false,
  "approved_by": "",
  "approved_at": "",
  "amazon_agent_policy_reviewed": false,
  "etsy_written_authorization": false,
  "etsy_authorization_reference": ""
}
```

Do not flip these fields without legitimate review. Etsy requires **written**
authorization in addition. `approved_at` must be a current ISO timestamp (the
pilot rejects an approval older than 30 days). This local file is a recorded
operator attestation, **not a cryptographically verified platform grant**.

Then, for an actually approved shop only:

```cmd
node src\browser_pilot.mjs --platform amazon --shop-url "https://www.amazon.com/stores/ExactShop/page/ExactPage" --approval-file "D:\CAM\secrets\shop-approval.json" --profile-dir "D:\CAM\profiles\bdna-amazon-shop" --output "D:\CAM\reports\pilot.json"
```

The profile is protected by an exclusive `.pilot.lock` file; if a process
crashes, inspect its state before manually clearing the lock. The output is
created with exclusive-create semantics, to prevent overwriting earlier
reports. No credentials or cookies are serialized.

## Fixed scan safety boundaries

- HTTPS only; Amazon supported storefront paths or Etsy `/shop/{name}` only.
- Reject cross-origin product links, suspicious queries, userinfo and
  unexpected redirects. Network route blocking rejects unapproved hosts and
  non-shop/product navigation. CDN allowlisting covers visible images only.
- Max **5** product pages per local pilot; at least **5 seconds** between
  page visits (bounded crawl, not a human simulation), one browser context.
- DOM-oriented read-only extraction. Store title, seller/platform listing
  identifier **if present in URL**, URL reference SHA256 and *image URL*
  SHA256. **No media download**: URL SHA256 is not an image-byte hash.
- CAPTCHA, sign-in and bot challenges trigger `needs_operator`; there is no
  automated resolution. Discovery and selector failures mean `partial` /
  parser review, not complete coverage.
- No pagination beyond one storefront landing page yet. Variants, sales,
  reviews, exact store coverage, rights to re-use content and Best Seller
  observations remain **unknown**.
- No browser task leases, cursor-outbox, gallery-media upload or automatic
  job promotion into CAM yet. Production scanner flag remains off.

## Test matrix and promotion gates

`npm test` verifies scope-gated URLs, supplier policy grants, redirect checks,
challenge handling, mock Chromium lifecycle, product title/image DOM metadata,
single-profile lock cleanup, and existing heartbeat/outbox behavior. Unit tests
use mock browser objects and do not visit marketplaces. A true Windows
Chromium integration test with **approved shop** and policy review is pending.

Before production rollout: approved platform access evidence, browser fixture
QA for current Amazon/Etsy DOM, fixed-profile protection, operator pausing,
lease/result outbox, signed gallery delivery, audit trail and data retention,
tenant-scope penetration tests, canary/rollback. Never equate a connected
heartbeat to a working scanner.
