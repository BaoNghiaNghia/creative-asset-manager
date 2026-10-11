# RRUGC — Always-on discovery with intermittent Windows Scout Manager

**Design status:** Approved architecture proposal for implementation, **not a claim that Auto Discover is live**.
**Created:** 2026-10-11. **Scope:** CAM Stage 0, Windows Scout Manager (Tauri), independent Agent-Reach research runner on production VPS.

## User contract

1. User is free to launch, pause, close, crash or reboot the **Windows Scout Manager** without affecting VPS Agent-Reach jobs, data, schedules, or already queued research.
2. **VPS Agent-Reach is an opt-in server service**, not a child of the Windows application and never controlled by Windows close/minimize/tray/Run Automation. Until explicitly enabled/provisioned, UI must say `Not configured`, not `Running 24/7`.
3. Windows Scout Manager starts **no** Review/Keyword child processes until user clicks **Run automation**. Restarting the Manager must not silently start them. This already matches `apps/scout-manager-desktop/src-tauri/src/lib.rs` `automation_enabled=false` at creation.
4. `Pause` and `Stop` controls are **scoped**: `Pause Windows Scouts` stops only local workers; `Pause Auto Discover` stops only VPS discovery; `Pause All Discovery` is separately permissioned and explicitly confirmed.
5. Both paths publish to one tenant-scoped **Stage 0 keyword identity**, retaining separate per-source evidence; do not create extra AEBrowse work because more than one source found the same phrase.
6. AEBrowse is financially free **for the user's existing third-party connection**, but constrained by provider throughput, errors, availability and fair-sharing. Do not invent a billing limit; apply technical rate limits and idempotent cached volume lookups.
7. Manual review is reserved for selecting designs/approving risky phrases, not for keeping the discovery research service alive. New keyword auto-volume enrichment needs its own operator-approved policy and idempotent job gate, not an unreviewed change to the existing Research Inbox export contract.

## Current implementation facts (observed 2026-10-11)

- `apps/scout-manager-desktop/src-tauri/src/lib.rs`: `Controller` starts with `automation_enabled=false`, starts Review + Keyword only after `Run automation`, manages Python/PowerShell subprocesses under a Windows Job Object and kills child trees on stop/quit. Restart/recovery are local.
- `apps/api/app/modules/realistic_review_ugc/query_intelligence.py`: Pinterest query leases are **45 minutes**; `renew_query` requires current owner and token. A hard Windows stop can leave a lease until expiration. Dashboard distinguishes old leases.
- `apps/api/app/modules/realistic_review_ugc/model.py`: `rrugc_keyword_volumes` has unique `tenant_id + keyword_normalized`; `rrugc_scout_queries` also unique by tenant + query; do not change legacy source_pin_url identity in place.
- `integrations/agent-reach/research_intelligence.py`: RSS/curated research is a **bounded manual cycle**, with local SQLite outbox and scoped CAM agent sync. `research-cycle` isn't scheduled. Exa/marketplace auto-search is NOT active. Distinct agent pairing and restricted runtime are still prerequisite for unattended ingestion.
- `apps/api/app/modules/realistic_review_ugc/research_intelligence.py`: tenant-scoped research-signals import, unique tenant + source/URL fingerprint, manual review and read-only export. Pinterest/Etsy/Amazon listing URLs are normalized; ingestion does not independently verify product content.

## Target topology

```text
Windows PC  [OPTIONAL]                          VPS  [ALWAYS-ON WHEN ENABLED]
Scout Manager                                 research-discovery.service
  ├─ Review Scout (local job object)              ├─ Exa / permitted public web discovery
  └─ Pinterest Keyword Scout                      ├─ provider host budgets, retry/circuit breaker
         │                                       └─ durable outbox + scoped agent token
         └─────── HTTPS ──────────┐                │
                                  ▼                ▼
                         CAM API / PostgreSQL (AUTHORITATIVE)
                         ├─ source evidence per provider/item
                         ├─ normalized quote identity + multiple provenance links
                         ├─ stage 0 keyword volume cache / dedup job
                         └─ distinct status / backpressure lanes
                                      │
                                      ▼
                                 Stage 0 UI
```

**No dependencies** on Windows uptime, WebView, Chrome profiles, user OAuth sessions, or open app windows in the VPS service. No Windows-to-VPS remote process control on launch/quit.

## Lifecycle and failover matrix

| Scenario | Windows Scout | VPS Auto Discover | Durable data / Stage 0 |
|---|---|---|---|
| Manager closed normally | Best-effort stop and release owned leases | Continues on schedule | Saved keywords intact; research persists |
| Manager minimized/hidden to tray | Follow actual Run/Pause state | Unchanged | No implicit resume |
| Windows crash, process killed, reboot or no internet | Local children end; may leave leases | Continues unaffected if VPS/network healthy | Retry expired leases; never reset completed runs |
| User reopens Manager | Shows stopped state; requires **Run automation** | Continues | Claims only pending, eligible work |
| Both active | Pinterest query + Review work | Web discovery at independent budget | Same normalized keyword becomes multiple source evidence entries |
| VPS research outage | Windows Scout keeps operating | Error/backoff/restart if configured | Persistent outbox and pending queue survive |
| CAM API/database temporarily down | Scout retries boundedly | Research worker retains outbox and retries | No loss after acknowledgment; no false successful sync |
| Exa rate limit / marketplace indexed page blocked | Windows unaffected | That source backs off with 429/403 rules | No unbounded retries or browser bypass |
| Gemini pool saturated | Existing Scout backpressure remains | Text-only web discovery continues; image/OCR work pauses | Do not let research starve review queue |
| Disk pressure on VPS | Windows unaffected | Stop new large downloads; metadata-only mode or pause research | Do not evict authoritative PG data |

### Stop, heartbeat and lease recovery (recommended implementation)

- On Windows `Pause` / `Quit`, **first block new claims**, then allow a bounded graceful shutdown notification (`stopped`) and release/requeue the caller's query/priority leases as **retryable** without counting an empty scan as failure. Do not turn Stop into an indefinite wait or freeze the UI.
- Existing `45 min` Pinterest lease is retained until explicit rollout of a short-lived lease + heartbeat/renewal protocol. Suggested new version: **3-minute lease**, **30-second renewal** and safe requeue after expiration. This is **not deployed** and requires tests with real long-running Pinterest detail expansion and outage simulation. Token+agent ownership and compare-and-swap remain mandatory.
- If process is killed without shutdown handshake: recover only on lease expiration, then reassign. Never force-expire a live other agent's lease from a stale UI timer. On resume, search result duplicates are resolved idempotently using persisted Pin ID and normalized keyword; save checkpoint before acknowledging completion.
- Liveness: last observed agent heartbeat plus separate stale thresholds (`running`, `stopped_by_user`, `connection_lost`, `unhealthy`, `not_configured`). Missing heartbeat alone **cannot prove intentional stop**; use `connection_lost` if no explicit stop receipt.
- A lease expiry is recovery safety, not proof the previous agent crashed; stale responses may arrive and must be rejected by mismatched lease token.

## Exactly-once *effect*, at-least-once *delivery*

- Keep stable `(tenant_id, platform, marketplace, listing_id)` per source item, plus immutable evidence IDs and last-seen observations; normalize quote across providers as its own identity, with many-to-many references.
- Database uniqueness and atomic insert/upsert arbitrate two Scouts racing to discover one phrase. `seen again` updates evidence association, not keyword count; never overwrite a better image/provenance with `None` from text-only search.
- Third-party AEBrowse volume work is enqueued only on missing or stale keyword volume. One tenant-scoped keyed job/lease per normalized phrase; many sources may wait on that job. Cache provider data and retain retryable errors. No separate volume calls per Amazon/Etsy/Pinterest result.
- VPS outbox writes accepted evidence before marking provider cursor complete. CAM API sync responds with full integer acknowledgments; partial or unknown receipts leave records retryable. Bounded batches, no silent drop, no token in logs.
- Backpressure is **per lane**: Windows Review Gemini and Pinterest Keyword maintain existing protection; Agent-Reach text-search uses its own search quota and gets a lower priority for optional image AI; PostgreSQL/AEBrowse share load-safe service-level gates.

## UI design

**CAM Scout Manager (web):** Three status cards (Review Scout / Pinterest Keyword Scout / Agent-Reach Auto Discover), each with dedicated Running / Idle / Paused / Offline / Error / Not configured state. Agent-Reach must be displayed as **Server / VPS** rather than `Windows Offline`. Counters: new phrases 24h, total unique phrases, evidence count, duplicates suppressed, last successful scan, next scan, 429/circuit breaker, unprocessed volume queue. Source chips Pinterest / Amazon / Etsy / All. Approval status is independent of discovery state.

**Windows desktop manager:** Existing two native cards retained. Add a **read-only** `Server Discovery` strip with last VPS heartbeat and new discovered keywords; clicking Windows `Run automation` or `Stop` never toggles server service. It must remain usable offline, with `Status unavailable` instead of stale `Running`.

**Global semantics:** `Scouts offline` on Windows means desktop machine offline **only**, not all keyword discovery stopped. Distinct role-protected actions: `Pause Windows Scouts` from Windows; `Pause Auto Discover` from CAM; `Pause all discovery` with confirmation. Display `stopped intentionally` only on authoritative stop event. Mobile/tablet/desktop must follow `CAM_MULTI_DEVICE_STANDARD.md` and corresponding QA gate.

## Default operational budgets (proposed, adjustable)

- VPS Agent-Reach web search: 1 concurrent worker, bounded scheduled cycles (e.g. 1/h), source-specific QPS/day budgets from the *actual configured search provider*; cache queries and reuse prior results. Do not assume unlimited free web search.
- Do not accelerate automatically merely because the Windows Scout is off; this would turn a discretionary shutdown into increased API usage. Adaptive throughput only within configured, monitored rate budgets.
- AEBrowse cache keyed by tenant+normalized phrase and freshness; retry 429/5xx using jitter and provider Retry-After, fail closed on credential/auth errors.
- VPS storage: metadata first, avoid full-resolution downloads unless explicitly licensed and permitted; bound temp retention, log growth and disk usage. Existing VPS disk pressure makes unbounded image mirrors unsuitable.

## Implementation order / gates

1. **Contracts/tests:** lifecycle scenario matrix; classify Windows stop, crashed lease, stale worker response, simultaneous duplicate keyword, VPS failure. No schema change first.
2. **VPS service:** explicit enable flag, dedicated scoped agent secret, non-root systemd timer/worker, secure metadata-only web discovery and outbox sync; health/status API with RBAC; no coupling to Windows.
3. **Lease hygiene:** add best-effort Windows graceful release API using existing owner/token guard, then shorten TTL only behind version capability flag after soak. Maintain old clients compatibility.
4. **Shared source evidence + AEBrowse queue:** additive migration, quote dedup across platforms and source association; protect the current Stage 0 table / approval state; AEBrowse technical budgets, not fictional billing limits.
5. **UI/QA:** 3-card dashboard, distinct controls/read-only VPS state on Windows, browser fixture test desktop/tablet/mobile, Windows native application QA; simulate shutdown/restart/internet loss/dual-running.
6. **Canary:** VPS service dry run, 25–50 public indexed items, limit one search worker, actual paired non-root identity, stop/start Windows many times, verify no lost work or extra AEBrowse calls, then enable service for production.

No auto-start, new credentials, network/search scheduler, schema migration, or deployment to live systems is authorized by this design document itself. Do not treat `docs/` presence as proof production Auto Discover works. Rollback: disable VPS Agent-Reach discovery feature flag and stop its service, leaving Windows Scouts and stored evidence intact; revoke research-only pairing without touching Pinterest pairing. Database additive tables retained, no destructive downgrade.
