# Agent-Reach Auto Discover V4 — adaptive queries and bounded title checks

## Design (2026-10-15)

**Main loop is free/keyless and remains VPS-only.** The existing systemd timer
keeps its hard cap of two provider searches per invocation and 20 searches
per UTC day. No retries that bypass provider quotas, rotating IPs, scraping
search result pages, model invocations or marketplace sign-ins.

- The SQLite outbox gains a constant-size `discovery_query_metrics` table,
  indexed by the 24 immutable search query IDs. Each successful search records
  distinct NEW canonical marketplace listing URLs, repeated URLs, and
  zero-yield runs. Failures do not falsely lower the novelty yield.
- A smoothed novelty score prioritizes previously useful queries. When two
  queries can run, a separate exploration slot prefers a less-tested query
  from a different marketplace. Ties rotate by the existing cursor, and
  queries continue to run at most once per UTC day.
- Existing canonical URL dedup and durable outbox receipts remain in force.
  No existing queued, synced, manually approved or rejected record is deleted.
  There is no assertion that a product title proves stitched text.
- A third, **optional** title-only check uses installed Playwright Firefox.
  `CAM_RESEARCH_PLAYWRIGHT_VERIFY=1` opts in; default is **off**. Only new
  canonical Etsy/Amazon/Pinterest product links whose snippets are ambiguous
  are checked, at most **one page per timer run / four attempts per UTC day**.
  No arbitrary hosts, sign-ins, proxy/IP rotation, anti-bot bypass, external
  screenshots, scripts, images, font fetches, automatic browser downloads or
  cookies. Requests stay within the allowlisted listing host and only main
  documents are allowed. A failed/inaccessible browser leaves the unverified
  result intact; each URL is journaled before launch and never repeatedly
  retried. The page title is NOT proof of embroidery or authenticity.
- Browser checks are entirely separate from the search budget. Existing
  read-only status now exposes cumulative query yield and daily browser
  attempts/title-check successes.

## Browser activation prerequisite

The VPS service runs as `cam-research`, not root. Install Node, the
`playwright` package and a compatible Firefox executable only into an
explicitly authorized, isolated runtime that this service account can read.
The preinstalled root browser cache is **not** a service-account dependency.
Use `verify_public_marketplace.cjs` only for item-level titles; do not enable
it until the browser binary is accessible and a live test with publicly
permitted pages passes. No new browser/model download is included in this
release because the current 40 GB VPS is close to full.

The optional code resolves Playwright from the project client installation;
production release installations which do not include that module will
gracefully record `unavailable` until an approved runtime is supplied.
Do not place API keys in child-process environment variables. Never claim
that disabled or unavailable Playwright performed a verification.

## Runbook / safety

1. Check the existing systemd timer and API health; preserve original
   hourly cadence and maximum 20 searches/day.
2. Release backend through the standard immutable release script. This
   feature uses an additive SQLite local schema, no PostgreSQL migration.
3. Let the next normal scheduled cycle update novelty statistics without
   extra Tavily requests; observe `query_performance` in the read-only
   `auto_discover.py status` result.
4. Keep Playwright **OFF** pending a low-privilege browser QA and website
   usage review. When enabled, observe `browser_attempts_today` and
   `browser_title_checks_today`. A low verification success rate should
   disable the optional browser, not increase requests.
5. Rollback: revert the worker code via normal backend release rollback and
   leave additive SQLite tables in place. Do not delete the research DB or
   clear provider rate-limit history.

## Current disk observations (workspace-scoped only)

On the VPS, `df -h .` reported a 40 GB root filesystem using 34 GB (91%)
with approximately 3.5 GB free. The checkout contains ~190 MB fixture/QA
artifacts, ~141 MB frontend node_modules, ~175 MB Scout Manager
node_modules and ~241 MB API virtualenv. These directories cannot all be
treated as unused; API venv is required, and QA evidence may be protected.
`scripts/cam-clean-browser-qa.py --dry-run` reported **0 eligible run
folders / 0 MiB** to clean. Official backend releases already prune older
unprotected versions. Host policy prevented direct inspection of external
system/root cache and other service directories. No bulk deletion or
unverified cleanup has been performed.
