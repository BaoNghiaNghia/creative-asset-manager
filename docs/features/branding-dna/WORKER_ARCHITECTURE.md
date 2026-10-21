# Branding DNA — Dedicated Worker Architecture (browser-first ADR, partial implementation)

**Decision:** Build a dedicated **Branding DNA Scanner** process with its own Playwright runtime and browser profiles, hosted on a separate authorized Windows machine (or isolated Windows VM). **Reuse Scout Manager's deployment/monitoring patterns, not its Pinterest scraping code or Chrome profiles.** CAM VPS stays the central API, database, queue, Gemini and profile aggregator. No live scanner has been installed yet.

## Implementation checkpoint — 2026-10-21 recovery

The finite Windows browser pilot now preserves successful local observations
when later parsing/navigation/cleanup fails. JSON reports remain authoritative
over SQLite progress; stopped reports do not become CAM evidence. Main-document
403/429 is latched, and uncertain browser cleanup retains its exclusive lock.

Scout Manager 0.1.42 source supervises only the paired CAM standby child.
Start, Stop, update exclusion and retry state share its process mutex. Four
rapid launch attempts exhaust the budget; it resets after a 60-second stable
child or an explicit Start. The supervisor never launches browser pilots or
Pinterest, and worker lease/dispatch remains a separate unimplemented gate.

Native Windows integration and an explicitly approved browser QA run are still
required before field release. Linux policy tests do not satisfy those gates.

## 1. Evaluated options

| Approach | Advantages | Disadvantages / risks | Decision |
|---|---|---|---|
| A. More Chromium browsers on CAM VPS | One host, fast internal API | Competes with API, image workers, visual encoder, low disk headroom, operational blast radius | **Reject** |
| B. Add shop crawler as another mode in existing Pinterest Scout process | Some code reuse | Regression risk, mixed browser profiles, Pinterest backpressure and shared restarts | **Reject** |
| C. **Dedicated Branding DNA Windows worker**, managed as a separate card/process in CAM Scout Manager | Isolated process, existing experience with Windows Chrome profile lock, local repair, updater and telemetry | Separate agent, packaging, authenticated provisioning and health checks must be implemented | **Preferred** |
| D. Linux mini-PC / isolated VM dedicated to Playwright | Scalable per-container/service, works headless, no Windows licensing dependence | New runtime and support path; platform behavior and login management need verification | **Later alternate** |
| E. External hosted scraping-as-a-service | Low provisioning effort | Third-party data handling, fees, availability, policies, unpredictable platform access | Not default |

**Note:** The user's Windows Scout workspace can be offline/unavailable while CAM production is online. A dedicated machine must enroll and heartbeat independently. Do not co-schedule this workload on a workstation already running dozens of unrelated automation profiles without measured capacity approval.

## 2. Runtime responsibilities

```mermaid
flowchart LR
  CAM[CAM UI and RBAC] --> API[CAM API / PostgreSQL]
  API --> Jobs[Durable jobs + per-shop lease]
  W[Dedicated Windows Branding DNA Worker] -->|TLS long-poll, scoped token| Jobs
  W -->|DOM navigation with explicit platform approval| Sites[Amazon and Etsy shop pages]
  W -->|Evidence manifest + hashes| API
  API -->|Bounded enqueue| G[Existing Gemini pool]
  G --> A[Profile aggregation + evidence claims]
  A --> Review[Human review and immutable approval]
  Review --> Skills[CAM Skills / generation binding]
```

**Worker package:** `apps/branding-dna-worker/` exists, with pinned Node 22.13+ and Playwright Chromium as an optional installed runtime on the Windows host. Local browser pilot runs separately from the heartbeat; central task dispatch is still disabled. Separate lockfile, test fixtures and Windows release artifact. Use a shared `worker-management` protocol adapter only for heartbeat, version/update display and log transport. Never import Pinterest site selectors, queries or history.

**Machine baseline for pilot (planning assumption, not benchmark):** 4–8 physical CPU cores, 16 GiB RAM, SSD/NVMe with 30 GiB free *dedicated* to worker, reliable network. Prefer 32 GiB if concurrent browser jobs and gallery captures are planned. Start at **one browser context / one active shop / one listing navigation at a time** with memory/CPU ceilings. Increase toward 2–3 contexts only after 48-hour profiling; there is no promise of a fixed number of concurrent products based on RAM alone. Browser binaries, logs and captures must have disk quotas.

**Separation:** Worker Windows service or Manager child process runs under an unprivileged OS user; browser state under a dedicated `%LOCALAPPDATA%/CreativeAssetManager/BrandingDNA/profiles/<tenant>/<shop>/<platform>` data tree. Exclusive lock per profile; never point Playwright at the user's normal Chrome profile, Pinterest profiles or shared cookies. Separate temporary downloads, logs, structured JSON spool and process supervisor.

## 3. Control-plane and data-plane protocol

- **Registration:** Administrator enrolls machine with one-time short-lived code; server returns revocable agent identity + scoped credential stored in OS protected secret storage. Rotate and revoke independently of Scout Manager's Pinterest credentials.
- **Outbound-only:** Worker polls `/api/v1/branding-dna/agents/lease` over TLS (example **proposed**, not implemented); no open inbound machine port, no arbitrary remote desktop.
- **Lease:** Tenant-scoped `job_id`, `lease_id`, `attempt`, deadline, adapter version and allowlisted URL/scope. Time-limited lease with heartbeat renewals; expiration can requeue. Compare-and-swap claim and fencing token prevent late worker uploads from overwriting newer attempts.
- **Progress:** Append idempotent `event_id` and `listing_revision` events; worker local SQLite outbox persists unacknowledged payload manifests during CAM outages. Replay ACK removes outbox entries.
- **Credential boundary:** Worker never receives Gemini tokens, global CAM API secrets or credentials for other shops. Browser local storage/cookies remain local to the authorized shop profile; do not transmit in screenshots, logs or diagnostics.
- **Pause:** Stop claiming new listings; finish current safe transaction, persist cursor; Cancel requests best-effort termination without data loss. Hard kill is reserved for runaway browser process.
- **Kill switch:** Disable by tenant, shop, platform and agent; all new leases denied immediately and active sessions drained. Maintenance mode cannot keep a scan appearing as running without heartbeat.
- **Manual block handling:** `needs_operator` for CAPTCHA/login/rate limit restrictions; no challenge bypass, stealth plugins, proxy rotation to evade policy, automated login or headless detection evasion.
- **Idempotency:** `tenant_id + platform + canonical_shop_id + listing_id + variant_id + source_revision + task_type + model_version`; exactly-once **effects** through server idempotency, not claims that delivery never duplicates.

## 4. Source adapter strategy — Browser-first, no marketplace APIs (2026-10-16 decision)

The user supports **Amazon and Etsy only** and explicitly chooses a dedicated
browser agent rather than marketplace APIs or third-party scraping services.
Shopify CSV/legacy API remains for backward compatibility but is not the path
for further development. CAM's **own API** is retained for internal RBAC,
heartbeat and audit, not for accessing marketplace data.

| Source | Browser method | Mandatory boundary | State |
|---|---|---|---|
| Amazon public storefront | Dedicated Playwright Chromium, DOM-visible shop cards and bounded product pages | Agent User-Agent, policy/robots review, explicitly approved shop; no challenge or block circumvention | Local 5-page pilot available, server dispatch OFF |
| Etsy public shop | Dedicated Playwright Chromium, DOM-visible listings and bounded detail/gallery previews | **Express Etsy written authorization** and reference in addition to shop-specific approval | Local 5-page pilot available, server dispatch OFF unless authorized |
| Private seller/account dashboards | Not included | Do not auto-login or access private pages through pilot | Unsupported |
| Shopify | No new work | Preserve existing records and legacy CSV endpoint | Deprecated as pilot focus |

Browser navigation runs with strict host/path allowlists, restricted resource
hosts, an isolated profile/lock, and max five pages at fixed bounded pace.
CAPTCHA or sign-in stops as `needs_operator`. No external data fetch services,
proxy rotation, stealth or marketplace API calls are implemented in agent code.
The visited site may issue its normal JavaScript requests as part of rendering.

References:
- [Playwright persistent browser contexts](https://playwright.dev/docs/api/class-browsertype)
- [Etsy published restrictions on browser automation](https://www.etsy.com/legal/api/)
- [Amazon Agent Policy](https://sellercentral.amazon.com/help/hub/reference/external/GS83KH2MA7HM69PH)
- [Pilot implementation, setup and safety gates](./BROWSER_AGENT_PILOT.md)

**Important:** All output is `browser_dom_observation`, rights/sales unknown,
coverage partial. A browser pilot does not certify a marketplace's permission,
a complete catalog, ownership, or Best Seller performance.

## 5. Suggested job contract

```json
{
  "job_type": "brand_listing_extract",
  "tenant_id": "tenant-id",
  "shop_id": "shop-id",
  "run_id": "run-id",
  "listing_key": "platform:shop:listing:variant",
  "source_url": "https://platform.example/allowed-listing",
  "source_revision": "etag-or-normalized-hash",
  "scope": ["metadata", "gallery"],
  "lease_id": "server-generated-opaque-value",
  "attempt": 1,
  "timeout_seconds": 120
}
```

This is an **illustrative contract**; the real server must generate lease and IDs, validate URL/redirect/DNS/IP against SSRF and canonicalize before dispatch. Never accept arbitrary user-supplied URL tasks on the worker.

Pipeline: `shop_validate -> enumerate_listing_keys -> extract_one_listing -> ingest_manifest -> image_analyze -> aggregate_candidate -> review_profile`. Source/image analysis queues are isolated by job type, backpressure from shared Gemini capacity and explicit bounded concurrency. No full-store hard loop in one job. Each cursor/manifest can resume after reboot.

## 6. Worker lifecycle and failure classification

States: `unregistered, idle, leasing, active, pausing, paused, needs_operator, retry_wait, unhealthy, updating, offline, disabled`. Per-task states: `pending, leased, running, completed, retry, permanently_inaccessible, blocked, cancelled`.

Failed parser / missing selector -> `parser_changed`, quarantine adapter with sample page evidence; `429` -> honor Retry-After and adaptive backoff; `403`/challenge -> stop requesting that source and surface operator action. Differentiate `shop_enumeration_incomplete` from `listing_extraction_failed`.

Health measures: heartbeat freshness (for liveness), **last successfully committed product** (for productive progress), last error, lease drift, queue age, output bytes, browser restarts, CPU/RAM, disk, credential expiry and adapter version. Heartbeat alone is insufficient to call a scan healthy.

## 7. Controlled rollout and promotion gates

1. **Contract/mock mode:** fake fixture shops, no external network, tenant/RBAC/auth tests, idempotency + crash/replay tests, UI states and Browser QA.
2. **One explicitly approved Amazon storefront:** dedicated Windows Chromium pilot; identify Agent in User-Agent, respect service restrictions; compare visible sample to a separately confirmed product list.
3. **One Etsy shop with express written authorization:** then run restricted browser pilot; otherwise keep Etsy scan blocked. No Etsy marketplace API fallback.
4. **Optional public storefront pilot:** only after platform-specific legal/policy and operational review; limited URLs, not unbounded enumeration.
5. **Scale:** memory/CPU/browser session budgets, queue fairness, stress test and worker update rollback proven for at least 48 hours.

### Local pilot installation boundary

Browser runner code and package lock are in Git, but a dedicated Windows machine must still install pinned Node/Playwright/Chromium and pass QA. CAM production does **not** automatically install browsers, grant rights, lease jobs or execute scans. No marketplace API credentials are required or accepted.
