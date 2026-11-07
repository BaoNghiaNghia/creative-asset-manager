# CAM Etsy Picked - Hybrid Design Extraction V1

Date: 2026-11-07. Source: CAM main worktree and Windows D:\CAM Image Extension.
Rollout: implementation staged; external production execution remains OFF by default.

## Architecture

- Pick creates an idempotent Etsy extraction job only when CAM_ETSY_EXTRACTION_WORKER_ENABLED=1.
- The job type etsy_design_extract is external-only and explicitly excluded from normal CAM/Codex worker claims.
- Windows Image Agent uses standalone CPU Fast Extraction under D:\CAM Image Extension\agent, port 8793. No Python/GPU/API dependency.
- CPU outputs are RGBA PNG 4500x5400, quality review required. Difficult images go to needs_ai; do not automatically trigger ChatGPT web, Codex CLI or paid API.
- CAM image-agents endpoints use dedicated device tokens and strict tenant isolation, with immutable task/lease and SHA256 receipt.
- Job relay is opt-in outbound HTTPS, single lane with a local D: journal; ambiguous responses retry identical receipt bytes.
- Review assets require persistent CAM storage and tenant authentication. Every result remains print_ready=false, rights_status=unverified.
- Storage is **fail-closed**: the configured absolute root must already exist and be readable/writable. Missing storage, low free disk or quota exhaustion prevents new claims; returning PNG after quota exhaustion gets HTTP 507 without marking the job complete. Windows preserves its journal and retries later.
- Defaults: `CAM_ETSY_EXTRACTION_STORAGE_QUOTA_BYTES=2147483648` (2 GiB review buffer), `CAM_ETSY_EXTRACTION_MIN_FREE_BYTES=3221225472` (3 GiB disk reserve). Quota is for *all* review PNGs in the dedicated root. There is no automatic deletion or assumption of 200 completed designs/day.
- The CAM Etsy Spy UI has a separate **Windows Image Agent** pairing section. An admin can create a dedicated device, copy a one-time disabled `cam-config.json` into `D:\\CAM Image Extension\\.runtime\\extraction`, and enable that device only after the pilot. Scout tokens and their online counts remain separate.
- Unpick cancels pending, retry and processing extraction, but never deletes historical results.
- A new Pick after Unpick opens a fresh `pick_generation` even if source image URL is unchanged; stale old leases and receipts remain rejected.
- Expired processing leases are claimable by another paired Image Agent with a rotated token/attempt; a still-valid lease is never stolen.
- Windows outbound relay uses a per-device, process-exclusive lock on D: (plus stale lock recovery) to stop duplicate processing from two launches.
- The claim API enforces `CAM_ETSY_EXTRACTION_DAILY_CAP` (default 200; supported 1..1000) per tenant and Vietnam calendar day. The Windows relay backs off for 5 minutes on `daily_cap_reached`.
- Tenant-scoped read-only metrics: `GET /api/v1/etsy-spy/image-agents/overview` gives queued, processing, daily claim count, `needs_review`, and `needs_ai`. The Picked summary card shows a condensed snapshot.

## Offline-paired dedicated device (2026-11-07)

- The Windows tool may generate a random 64-character bearer token locally using `node agent/self-pair.mjs`. The token remains only in `D:\\CAM Image Extension\\.runtime\\extraction\\cam-config.json`, with `enabled:false`.
- Only the device UUID and SHA256 token digest are sent to the CAM production host. The operator runs `PYTHONPATH=apps/api apps/api/.venv/bin/python scripts/pair-cam-image-agent-hash.py --agent-id <uuid> --token-sha256 <digest>` on the authorized VPS; the script refuses SQLite, foreign tenant, token reuse or conflicting/revoked identity.
- `GET /api/v1/etsy-spy/image-agents/{agent_id}/probe` accepts only the dedicated device bearer credential and reports pairing/storage/capacity without claiming a job. Windows checks via `CHECK_CAM_IMAGE_PAIR.cmd` with the relay still off.
- Do not print, log, paste or transfer the plaintext token through agent handoffs. Device may be revoked through existing admin controls.

## Before production activation

1. Deploy from main only, run health checks and preserve a rollback release.
2. Storage on CAM is provisioned by `bash scripts/cam-etsy-extraction-storage-prepare.sh --check` and `--apply`, which creates `/var/lib/creative-asset-manager/etsy-design-extraction` (owner `creative-assets`, mode `0700`) and adds the storage root, 2 GiB quota, 3 GiB free-disk reserve, and a **1/day pilot cap** to the protected production environment. It does not set `CAM_ETSY_EXTRACTION_WORKER_ENABLED`, alter other secrets or restart the API. Run release deploy afterward to load the environment.
3. Pair a unique device from CAM `Etsy Spy → Windows Image Agent → Pair Image Agent`. This uses the dedicated `POST /api/v1/etsy-spy/image-agents` route with admin access. The one-time copied JSON stays `enabled:false` until the pilot and must be saved only to the Windows D: runtime config.
4. On Windows run SETUP_CAM_EXTRACTION.cmd; fill .runtime/extraction/cam-config.json with HTTPS origin, agent_id, token, tenant_id boostgame, enabled true.
5. Launch START_IMAGE_AGENT.cmd and START_CAM_EXTRACTION_RELAY.cmd without restarting Chrome or the existing Codex executor.
6. Only after a consented one-listing pilot, enable CAM_ETSY_EXTRACTION_WORKER_ENABLED=1 and verify upload/receipt, private preview, lease replay and role isolation.
7. Scale cautiously at 20 -> 50 -> 200 processed listings/day, measuring QA-passed artifacts separately.

## Commands

- Windows: node --test agent/engine-router.test.mjs agent/cam-client.test.mjs
- Windows: node agent/check.mjs ; node agent/status.mjs ; TEST_EXTENSION.cmd
- CAM: PYTHONPATH=apps/api apps/api/.venv/bin/python -m pytest -q apps/api/tests/modules/etsy_spy/test_etsy_spy.py apps/api/tests/modules/processing/test_repository.py
- CAM: npm --prefix apps/client run typecheck
- CAM: npm --prefix apps/client test -- --run app/etsy-spy/EtsySpyPage.test.tsx

## Non-goals

- No programmatic ChatGPT browser Send/Save/scraping, no changes to Codex CLI or existing bridge on 8791/8792.
- No background backfill of past Picked listings, no permission inference from Etsy trademarks.
- No print-ready claim based solely on PNG size. Commercial usage requires verified image rights and fidelity.
