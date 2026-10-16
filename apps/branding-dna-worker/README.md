# Branding DNA Windows Worker — Heartbeat + manual Browser Pilot

The default process sends signed **heartbeats and restricted lifecycle telemetry** with a durable SQLite outbox. A separate **manual local browser pilot** can navigate an individually approved Amazon/Etsy shop with Chromium/Playwright. `npm start` does not run it. Production scanner dispatch remains disabled; the pilot does not upload evidence to CAM.

## Setup (on an authorized dedicated Windows machine)

1. Install Node.js **22.13+** (required for built-in `node:sqlite`), pin the runtime, and copy this worker directory into its own Windows directory. Avoid using Pinterest Scout's Chrome profile or process.
2. In CAM Branding DNA → Dedicated Windows Worker, register an agent. The API shows an **agent ID and token once**. Store them in an OS-protected secret vault. Do not commit or log the token.
3. Inject the three environment variables only into the worker process: `BRANDING_DNA_SERVER_URL`, `BRANDING_DNA_AGENT_ID`, `BRANDING_DNA_AGENT_TOKEN`. Do not place secrets in the .cmd, repository or public configuration.
4. Run `npm test`, then `npm start` or the .cmd. CAM should show heartbeat/online within ~1 minute. To stop immediately revoke the token via Disable in CAM.

## Browser pilot — opt-in, local only

The package now pins Playwright **1.56.1**. On a dedicated authorized Windows host run `npm ci` and `npx playwright install chromium`; no browser binary is shipped or installed by the production heartbeat command. Launch manually via `node src/browser_pilot.mjs` only with an exact-shop, freshly approved authorization file and a dedicated profile. **Etsy requires express written authorization; Amazon requires Agent Policy compliance and agent identification.** Bounded to 5 product pages, no login/CAPTCHA bypass, no marketplace API calls. See `docs/features/branding-dna/BROWSER_AGENT_PILOT.md` for permission requirements, setup and example.

Node 22 marks `node:sqlite` experimental; validate the pinned runtime on the dedicated machine. Heartbeat success does not imply a productive scanner.

## Durable lifecycle telemetry (not scanner evidence)

- Windows database: `%LOCALAPPDATA%\CreativeAssetManager\BrandingDNA\<agent-id>\worker-outbox.sqlite`. On Linux QA hosts, `$XDG_STATE_HOME` or `~/.local/state`. Dedicated per-agent directory, symlink file rejection.
- Local SQLite WAL + `synchronous=FULL` writes; each lifecycle event is committed before delivery and deleted only after an **exact complete ACK** from CAM. Max 2,000 pending rows, batch up to 50; outbox-full fails closed without discarding old events.
- Accepted events are `started`, `stopped`, `heartbeat_failure`, `heartbeat_recovered` with fixed detail codes. No arbitrary messages, credentials, shop URLs, cookies, images, or user content can be sent.
- Server event endpoint `POST /api/v1/branding-dna/workers/{agent_id}/events` authenticates the existing agent Bearer token, tenant-scopes events, handles replay via event ID + digest, rejects conflicting duplicates, limits new events to 1,000 per agent/day and accepts timestamps at most 30 days old.
- Connectivity failures and invalid ACKs leave rows pending. HTTP redirect following disabled and 15s request timeout. Revoked enrollment fails closed.
- **Do not confuse this lifecycle outbox with the future per-task result outbox**. No scanner jobs are dispatched. Production database migration and Windows installer/update have not been performed. Node's built-in SQLite carries an experimental warning on Node 22 and requires a pinned runtime QA pass.

## Required upgrades for scanning

Tenant-bound lease-to-worker dispatch, browser selector fixtures against permitted live sources, task/result SQLite outbox, media rights and signed upload, operator pause/kill, retention, Windows Browser QA and canary rollback. The browser pilot and lifecycle outbox are implemented separately, but production job dispatch is intentionally disabled. See `docs/features/branding-dna/WORKER_ARCHITECTURE.md`.
