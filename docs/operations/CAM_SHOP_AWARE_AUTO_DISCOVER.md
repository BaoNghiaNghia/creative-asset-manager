# CAM Shop-Aware Free Auto Discover — staged rollout (2026-10-22)

## Objective and scope

Reuse **existing** Agent-Reach Auto Discover, the paired `Agent-Reach Auto Discover`
RRUGC Scout identity, CAM Branding DNA Shop Library and tenant-scoped Research
Inbox. No new extension, marketplace crawler, paid provider, marketplace API
key, frontend, API endpoint, PostgreSQL migration, or Linux daemon.

The Shop Library currently contains 33 Etsy/Amazon records in the tested CAM
tenant (32 Etsy, 1 Amazon). Each eligible CAM shop may seed a *public web search*
for already-indexed product links. No Etsy/Amazon page is fetched or browser
opened by this feature. Indexed results are unverified suggestions and do
**not** prove a listing belongs to the shop, features actual embroidery,
was a best seller, has verified sales, or is licensed for reuse.

## Delivery phases and gates

| Phase | Work | Acceptance gate |
|---|---|---|
| A: Foundation | Read existing dedicated Scout pairing and CAM shop API; preserve tenant auth | Authenticated CAM endpoint stays read-only and tenant-scoped; no new API |
| B: Search selection | Validate canonical US storefront; ignore paused/unsupported; rotate least-recently searched shop | No duplicate shop attempt per UTC day, no injection, spoof, private URL, or cross-tenant fetch |
| C: Budget/inbox | Reserve at most one shop slot from existing two requests/cycle and 20 requests/UTC day; continue normal discovery | Search API calls *never* exceed old budget; SQLite journal precedes request; existing canonical URL dedupe and CAM ACK remain |
| D: Release | Run Python unit tests, CI, and production readiness review | All gates green; production flags off until separate reviewed activation |
| E: Canary | Enable one bounded shop query per hour on Tavily **Keyless only**; compare 25–50 unverified search hits with human review | No 402/429 abuse, no missed ACK, no secret leakage, no claim of verified shop membership |
| F: Operations | Monitor status and re-evaluate indexed coverage after several days | Zero paid calls, durable outbox, correct tenant; no guarantee every shop has indexable items |

Phase A–C implementation is isolated in `integrations/agent-reach/auto_discover.py`.
Phase D tests are part of CI. E/F are deliberately distinct production gates.

## Implementation and free-budget limits

- Config flag `CAM_RESEARCH_SHOP_QUERIES_ENABLED=1` (default `0`).
  It applies **only** if the selected search backend identifies as
  `tavily-keyless`. Other backends continue the existing generic search
  unchanged and never receive a *new* shop-target query.
- To guarantee **zero paid search APIs for the whole worker**, require
  `CAM_RESEARCH_SEARCH_PROVIDER=tavily`, and do not configure an implicit
  paid fallback. No new API key or credit card is needed. Free availability
  is not guaranteed; provider 402/429/403 triggers existing cooldown.
- Each non-paused `active`/`draft` US Etsy `/shop/...` or Amazon US
  `/stores/...` is a bounded search seed. Unsupported Amazon country
  storefronts, Shopify and malformed records are ignored. Sanitized names
  become fixed `site:etsy.com/listing` or `site:amazon.com/dp` search
  queries. Search only; **no** shop HTML fetch, login, CAPTCHA handling or
  marketplace agent activation. The selector covers the small Amazon source
  early without subsequently starving the many Etsy shops.
- One query per run targets a shop when at least 2 provider calls are
  available; the other remains general discovery. If only one call fits
  the budget, alternate shop and generic. Under a *fully available*
  20/day search quota this is up to ~10 shops per day (~4 days for 33)
  but provider rate limits and empty results may delay discovery.
  Search results are **not** full per-shop catalog coverage.
- Before calling the provider, the shop ID/day is recorded in the
  existing low-privilege SQLite outbox database using additive tables
  `discovery_shop_attempt` and `discovery_shop_history`. All requests
  use the **same** daily total as `discovery_attempt`. Outbox and ACK
  behavior are unchanged; already-synced results stay durable.
- Only the dedicated paired Research Scout ID/token can read tenant-scoped
  shop records through `GET /api/v1/branding-dna/scout-agents/{id}/shops`.
  The origin is fixed HTTPS CAM production, no redirects or proxies,
  no raw page fetch and a bounded JSON response. If unavailable, use
  the original generic query plan; do not claim shop coverage.
- The UI Research Inbox remains the normal destination. No automatic
  inventory/stock write, manager approval, AEBrowse/Gemini spend or
  marketplace policy bypass. Existing Stage 0 conservative unverified
  candidate promotion is unchanged.

## Test and rollout commands

Run from a **separate reviewed checkout**, not production:

```bash
python3 integrations/agent-reach/test_auto_discover.py
python3 -m py_compile integrations/agent-reach/auto_discover.py
git diff --check
```

Before enabling on VPS, confirm the reviewed release of the script
is installed and these read-only checks succeed:

```bash
systemctl is-active cam-research-auto-discover.timer
systemctl list-timers --all cam-research-auto-discover.timer
journalctl -u cam-research-auto-discover.service -n 25 --no-pager
```

A permitted operations change may add the **non-secret**
`CAM_RESEARCH_SHOP_QUERIES_ENABLED=1` and pin
`CAM_RESEARCH_SEARCH_PROVIDER=tavily` through the reviewed systemd
environment configuration. Never paste tokens or dump secret env files.
Check `shop_catalog_status`, `shop_queries_today`, total
`queries_today`, `pending_outbox`, `synced_total` and
`last_error`. A provider cooldown is not a successful search.
Neither deployment nor activation should be asserted from unit tests alone.

## Risks, permissions, and rollback

- Marketplace data use: public search result links do not confer a license
  to scrape, mirror product images, copy seller content or infer sales.
  No Marketplace browser scans are enabled here.
- Tenant security: existing paired Scout auth + tenant-limited CAM SELECT;
  no new credential, no user cookies, no token in query or log.
- Local database: two `CREATE TABLE IF NOT EXISTS` metadata tables only.
  No PostgreSQL/tenant migration. Compatible with the previous binary/script.
- Unknown store linkage: a search match can be from another seller.
  All results remain unverified; never label them verified inventory/sales.
- Cost/availability: use Tavily Keyless only; quota may be unavailable,
  return 429 or change without notice. Do not evade provider limits.
- Rollback **immediately** by removing the flag (or setting
  `CAM_RESEARCH_SHOP_QUERIES_ENABLED=0`) and restarting only the
  independent VPS timer/service at a safe point. Fixed-query discovery
  and all stored research remain intact. If needed restore the prior
  reviewed `auto_discover.py` release without deleting SQLite tables,
  outbox rows or CAM records.
