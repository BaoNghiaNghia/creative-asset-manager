# RRUGC Agent-Reach Auto Discover — production operations

**2026-10-11 / delivery v2.** Runs independently of Windows Scout Manager. Exa and Brave are general web-search providers, **not** authenticated Etsy/Amazon/Pinterest catalog APIs. This phase collects item-level public search evidence into the existing tenant-scoped **Research Inbox**; operator review and existing Stage 0 keyword/AEBrowse ingestion rules are preserved. Do NOT claim that all discovered titles are verified quote embroidery.

## Runtime / safety boundaries

- Entry: `integrations/agent-reach/auto_discover.py once`. Only two fixed official search endpoints are allowed: Exa MCP (`https://mcp.exa.ai/mcp`) and Brave Search (`https://api.search.brave.com/res/v1/web/search`). No arbitrary page fetch, HTML scraping, browser cookies, Chrome processes, marketplace logins, models or AEBrowse calls.
- V2 cycles through 24 product-focused web queries for US Etsy, Amazon and Pinterest; at most two searches/hour and 20/day budget. Each query index is attempted once per UTC day. No hidden retries, unlimited pagination, or simultaneous workers. Review precision and provider billing before increasing either limit.
- Single-writer `fcntl` lock, restrictive SQLite WAL outbox on VPS, canonical platform URL + ID matching, resilient full-receipt sync. A completed sync is **never** assumed without complete added+duplicate acknowledgment. No result gets silently discarded on retry. Backend dedup fingerprint is `tenant+source+canonical URL`; this is distinct from later quote normalization across platforms.
- Anonymous Exa MCP is IP-rate-limited. Add an Exa Starter API key via `CAM_RESEARCH_EXA_API_KEY` (sent only in the official `x-api-key` header) to use account credit/quotas rather than the anonymous IP pool. Brave uses `CAM_RESEARCH_BRAVE_API_KEY` (official `X-Subscription-Token` header). Both providers can still return 429. Each gets an independent 6-hour cooldown, no proxy/IP rotation, evasion, unbounded retries or source-site scraping. Auto mode prefers Brave if configured and available; when Brave is cooling down and Exa has a dedicated key, the next timer tick uses Exa instead. Without a key, the bounded anonymous Exa path remains subject to its provider quota.
- The background service is a low-privilege systemd oneshot/timer; not a Windows child, not an autonomous arbitrary browser. No frontend events or local PC state affect it. If RAM, network, or disk is unavailable, it fails boundedly and retries next timer tick; the pending outbox remains intact.
- CAM-side `/research/auto-discover/status` is permission protected (read). `/research/auto-discover/control` requires RRUGC run permission and affects ONLY the dedicated VPS agent; Windows cannot pause it. A paired desktop can read `auto_discover` from its existing authenticated operations-summary without a new credential.
- Windows v0.1.18 still starts Scouts only when **Run Automation** is pressed. Graceful local Stop/Quit tries a 2-second authenticated best-effort lease release on tenant+agent-owned Pinterest query/priority leases; a crash is handled by existing 30–45 minute TTL. Late results are fenced via lease tokens.
- Research agent name MUST be exactly `Agent-Reach Auto Discover` and paired with a **dedicated token**, not the Windows Scout token. Before pairing, public research can run into the local outbox, but CAM status correctly shows **not configured** and no synced research. No impersonation or automatic privileged pairing from a public web request.

## Safe production install (after committing, pushing, releasing both API and frontend)

```bash
# On authorized VPS from a clean deployed release; root operator.
bash scripts/cam-install-research-auto-discover.sh
systemctl list-timers cam-research-auto-discover.timer
# Non-root service, no token required for initial public search.
systemctl start cam-research-auto-discover.service
journalctl -u cam-research-auto-discover.service -n 60 --no-pager
```

The installer creates `cam-research` system user and `/etc/creative-asset-manager/research-auto-discover.env` (0640 root:cam-research); secrets are never written to Git or the unit body. Runtime outbox lives at `/var/lib/creative-asset-manager-research/research-inbox.sqlite3`. Unit always uses `/opt/creative-asset-manager/current`, so server release upgrades do not require a new timer. No destructive retention action.

**One-time production pairing (required for CAM ingestion):**

1. In authenticated CAM Scout Agent management, create a new agent with **exact name** `Agent-Reach Auto Discover`; copy one-time ID and token securely.
2. Provision pairing keys into the root-owned env file via an approved secret-operator channel, not shell args/chat/history: `CAM_RESEARCH_SCOUT_AGENT_ID`, `CAM_RESEARCH_SCOUT_TOKEN`. For reliable discovery, configure at least one official search provider key: `CAM_RESEARCH_EXA_API_KEY` (Exa Starter includes recurring credit) or `CAM_RESEARCH_BRAVE_API_KEY` (Brave provides monthly credit with payment method). Optional `CAM_RESEARCH_SEARCH_PROVIDER=auto|exa|brave` (default auto). Do not provision marketplace credentials, share Windows tokens, disable quota caps, or enable Brave automatic top-ups without separate approval.
3. Start one service invocation and verify its journal reports the actual provider (`brave-search` or `exa-mcp`), `status=ok` and positive `added_last_run` (or genuine duplicate counts), with real canonical listing URLs. Confirm the dedicated pairing imports results into CAM Research Inbox. If provider quotas are exceeded, the journal reports `brave_rate_limited` or `exa_rate_limited` and subsequent cooldown; do not call cooldown a successful discovery. A local successful result without pairing proves collection but NOT CAM ingestion.

**Staged rollout:** Keep provider budget low at first, compare 25–50 real search results with human review, verify no orphaned windows, run Browser QA at desktop/tablet/mobile, and check disk pressure (VPS has limited free capacity). Follow company policy when republishing search snippets and listing metadata; do not mirror product images without rights.

## Runbook and rollback

```bash
# Read-only local status
apps/api/.venv/bin/python integrations/agent-reach/auto_discover.py status --database /var/lib/creative-asset-manager-research/research-inbox.sqlite3
# Pause only VPS Auto Discover: CAM Research Inbox > Tạm dừng VPS
# Or stop timer at the VPS, leaving the Windows Scouts and stored data untouched:
systemctl disable --now cam-research-auto-discover.timer
# For one-shot rollback, also stop any running oneshot:
systemctl stop cam-research-auto-discover.service
```

Runbook recovery: if `429` or `exa_rate_limited`, wait for cooldown or configure an Exa provider key. If Research Inbox remains empty while local outbox has pending rows, check dedicated pairing/token and API sync receipt; **do not reuse Windows secret or discard outbox**. If status is offline, distinguish intentional pause from missing heartbeat. Windows shut down abruptly: expect up to TTL before its owned query lease can be reclaimed.

**Not yet part of v1:** rich cross-platform quote clustering, marketplace API-level verification, automatic AEBrowse enrichment of *unreviewed* search snippets, or multi-source Stage 0 evidence schema. Those require additive model/worker work and validation before activation.
