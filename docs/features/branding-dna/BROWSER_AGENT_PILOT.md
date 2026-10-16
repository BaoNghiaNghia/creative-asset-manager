# Branding DNA — Amazon/Etsy Browser Agent Pilot

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

## Integrated Scout Manager mode (2026-10-16)

Branding DNA Browser Agent is available in the **existing Pinterest Scout
Manager** (`apps/scout-manager-desktop` v0.1.21) from the same
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
