# CAM Stage 0 — Multi-platform Market Intelligence (Architecture Proposal)

Status: **Phase 0 foundation implemented; marketplace discovery connectors NOT enabled** (2026-10-11).
Owner: Creative Asset Management / RRUGC. Scope: Pinterest + Amazon US + Etsy US initially, extensible.

## Decision: Agent-Reach orchestrates sources; it is not a universal marketplace scraper

Current deployed baseline: Agent-Reach is an isolated channel registry and bounded RSS research importer. The Windows Pinterest Keyword Scout is a separate existing operational agent. The Research Inbox is a tenant-scoped **suggestions gate**, not the Stage 0 volume database. Stage 0 stores Google Ads/AEBrowse search volume and a legacy `source_pin_url`. Directly adding more scrapers to the RSS adapter would conflate product evidence, keyword suggestions, and paid volume enrichment, and violate source identity and platform terms.

Use **source adapters** with authorization-specific capabilities:

| Source | Discovery access | Stable evidence identity | Status |
|---|---|---|---|
| Pinterest | Preserve existing local Keyword Scout; evaluate officially authorized Pin API access separately (API search may only cover authorized user's Pins) | Pin ID | Existing tool, not replaced |
| Etsy | Etsy Open API v3 `findAllListingsActive` with `keywords` and authenticated app key, proper marketplace access approval | Listing ID | Planned, requires Etsy app credentials |
| Amazon US | Amazon **Creators API** `SearchItems` for eligible affiliate use **or** seller-authorized **SP-API Catalog Items** for eligible selling partners, subject to use-case/license terms | ASIN + marketplace | Planned, requires authorized API access |
| Other sources | Pluggable read-only provider (e.g. licensed market data, permitted RSS/trend feeds); verify provider permissions before enabling | Provider-defined item ID | Future |

As of Oct 2026, Amazon's older Product Advertising API v5 is deprecated. Do **not** implement PA-API v5 or unapproved login/cookie automation as a default replacement. Amazon Creators API requires eligibility; SP-API is not a universal public catalog scraping key. Etsy has per-key QPS and sliding-24-hour QPD limits. Read official documentation before building a new connector.

## Proposed architecture

```text
Source Config (enabled, region, category, query sets, credentials reference)
   ├── Pinterest Scout adapter (existing local, isolated browser/profile)
   ├── Etsy Open API v3 adapter (not active)
   ├── Amazon Creators / authorized SP-API adapter (not active)
   └── Agent-Reach RSS / curated trend adapter (existing)
                 ↓
       Provider-specific normalizer
       URL / stable ID / title / listing metadata / provenance / observed_at
                 ↓
     Marketplace Evidence Store (PostgreSQL, tenant-scoped)
       unique(tenant,platform,marketplace,item_id)
       separate observations + keyword-to-evidence associations
                 ↓
   Cap/trucker-hat relevance filter → text/embroidered quote extraction
                 ↓
   Canonical quote + semantic duplicate clustering (across sources)
                 ↓
       Candidate priority queue / operator review
                 ↓  manual approval or governed policy
     Stage 0 Keyword Volume job (AEBrowse, cached 30 days)
                 ↓
    Existing Stage 1–5 unchanged
```

### Phase 0 (current change): safely accept source-attributed marketplace *links*

- Research Inbox accepts source `pinterest|etsy|amazon` **only** with HTTPS on exact approved primary domain and recognizable Pin/listing/product ID; it stores a normalized URL with query tracking removed. Source `web|rss|github|youtube` remains backwards compatible.
- This **does not fetch listings, prove the title was observed on the marketplace, or grant catalog data reuse rights**; these are unverified operator/submitted pointers. Persisting them never triggers AEBrowse, Gemini, image downloads, or auto-approval.
- Identity uses canonical URL within existing research fingerprint `tenant + source + URL`; no schema migration. The production source-catalog pipeline remains unchanged. Other Amazon country domains and Etsy country variants must be explicitly reviewed later.
- The isolated Agent-Reach staging importer and the API enforce the same canonical source/host rules. A QR/browser user session is not needed for the importer; durable unattended sync still requires a **dedicated** paired Scout Agent token and restricted non-root runtime.

### Phase 1: marketplace evidence schema (before automated connectors)

Implement additive versioned migration + repository/service/tests:

- `marketplace_source`: `tenant_id,platform,marketplace,enabled,authorization_state,query_plan_version,rate_budget,created_at`.
- `marketplace_evidence`: `tenant_id,platform,marketplace,external_id,canonical_url,source_title,source_excerpt,license_class,media_reference,observed_at,source_run_id,provenance_kind,validation_state,first_seen_at,last_seen_at`. Unique by `(tenant_id,platform,marketplace,external_id)`; never rely on title/URL for identity.
- `marketplace_evidence_quote`: `tenant_id,evidence_id,keyword_id,quote_text,normalized_quote,extraction_status,confidence,review_state`; many-to-many across platforms.
- `marketplace_source_run`: `tenant_id,source_id,query,run_id,started_at,finished_at,items_seen,items_new,duplicates,error_category,next_cursor`. Idempotent leases and checkpoints.
- Backfill legacy `source_pin_url` into evidence **without deleting or changing** the original field. Keyword uniqueness remains tenant-scoped and volume enrichment remains an independent pipeline.

### Phase 2: official connectors and query diversification

- Per-platform adapter interface: `discover(query, cursor, budget) -> evidence[]`; `health()`; `capabilities()`. No arbitrary browser sessions or user tokens crossed to other lanes.
- Query expansion: product-targeted terms `embroidered trucker hat`, `baseball cap quote`, `five panel hat saying`; add semantic variants and category constraints, not unrestricted broad crawl.
- Bounded scheduler: source-specific concurrency, QPS/QPD-aware budget, 429 Retry-After, exponential backoff + jitter, 401/403 disable until re-auth, heartbeat, lease timeout, last successful cursor.
- Dedup by canonical stable item ID **within each platform** and normalized keyword **across platforms**. Never merge separate listings because they share the same title or image.
- Store permitted excerpts/metadata and attribution links only. Images require explicit legal/provider rights and authorized download path. Do not bulk mirror marketplaces or send protected listing data to an external AI provider without licensing review.
- Cache provider results according to each API's terms; AEBrowse cache and Gemini extraction cache keyed by normalized keyword/content hash + model/prompt version; only enrichment policy triggers billed work.

### Phase 3: keyword scoring, review, and UI

- Separate **Keyword**, **Evidence**, and **Scout Jobs** views under Stage 0, unified provider badges and filter chips Pinterest / Etsy / Amazon / All.
- Evidence side panel: canonical link, source identity and timestamp, original product title, parsed quote, confidence, dedup cluster, compliance/trademark warning, and reason for recommendation.
- Provisional priority score = cap relevance (25%), textual quote confidence (20%), independent source breadth (20%), growth/volume signal (20%), novelty (10%), freshness (5%). **Heuristic only**; do not call it predicted sales or real marketplace demand.
- Distinguish `marketplace listing count` from `Google monthly search volume`; a common quote on Etsy is not proof of search volume or revenue.
- Review policy: `discovered → filtered → extracted → candidate → operator approved → volume_queued → enriched`. Rejected/AI/blocked feedback suppresses future expansion of that exact query/source, with audit and reversible operator actions.
- Dashboard cards per connector: enabled, auth healthy/expired, backlog, scanned/new/duplicate, 429 rate limit, estimated API cost, AEBrowse calls prevented by cache, extraction queue and error retries.
- Desktop/tablet/mobile parity required; horizontal tables may scroll in their own labeled region. Actions and review remain operable on touch and keyboard.

## Safety gates and rollout

1. Use an isolated staging dry run (25–50 marketplace records per source), no paid calls, no silent auto-approval. Check provenance, keyword dedup, timeouts, storage growth and rights.
2. Enable Etsy with operator-authorized app key first, respecting rate headers. Amazon second after concrete eligible API path and licensing. Pinterest Scout stays running independently.
3. Evaluate success by `new reviewable unique phrases / 100 source records`, false-positive quote rate, duplicate reduction, fresh sources, human approval precision, provider 429 rate, and actual API cost. Do not optimize only for raw keyword count.
4. Before promotion, run database migration/rollback review, negative tenant-RBAC/SSRF tests, queue retry/idempotence tests, browser fixture QA for phone/tablet/desktop, immutable production release scripts + /live /ready smoke.
5. Without per-platform API credentials and rights, show connector status `Not connected`, never pretend Amazon/Etsy search is live. Do not install cookies, seller app permissions or background schedules without explicit operator authorization.

Official references:
- https://developers.etsy.com/documentation/reference (findAllListingsActive; keywords)
- https://developers.etsy.com/documentation/essentials/rate-limits/
- https://affiliate-program.amazon.com/creatorsapi/docs/en-us/introduction
- https://affiliate-program.amazon.com/creatorsapi/docs/en-us/paapiv5-deprecation
- https://developer-docs.amazon.com/sp-api/lang-en_EN/docs/manage-product-listings-guide
- https://developers.pinterest.com/docs/api/v5/pins-list/

Rollback for Phase 0: revert code; no migration, scheduled searches, or secret changes. Existing rows are unaffected. Phase 1 tables are additive; disable connector flags and retain evidence on rollback. Never destructively downgrade live data.
