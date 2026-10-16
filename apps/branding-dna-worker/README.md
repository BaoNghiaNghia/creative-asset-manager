# Branding DNA Windows Worker (control-plane bootstrap)

This process sends signed **heartbeats and restricted lifecycle telemetry**. A durable SQLite outbox retains operational events across outages/restarts and replays them until an exact server ACK. The scanner remains disabled: no product scans, browser navigation, image processing or real job leasing.

## Setup (on an authorized dedicated Windows machine)

1. Install Node.js **22.13+** (required for built-in `node:sqlite`), pin the runtime, and copy this worker directory into its own Windows directory. Avoid using Pinterest Scout's Chrome profile or process.
2. In CAM Branding DNA → Dedicated Windows Worker, register an agent. The API shows an **agent ID and token once**. Store them in an OS-protected secret vault. Do not commit or log the token.
3. Inject the three environment variables only into the worker process: `BRANDING_DNA_SERVER_URL`, `BRANDING_DNA_AGENT_ID`, `BRANDING_DNA_AGENT_TOKEN`. Do not place secrets in the .cmd, repository or public configuration.
4. Run `npm test`, then `npm start` or the .cmd. CAM should show heartbeat/online within ~1 minute. To stop immediately revoke the token via Disable in CAM.

## What is not installed/enabled

There is **no Playwright/browser binary dependency yet** in this control-plane package. Node 22 marks `node:sqlite` experimental; validate the pinned runtime on the dedicated machine before a production worker upgrade. Pin and install Playwright in the isolated worker during the authorized adapter phase after platform permissions and task leases are implemented. Do not interpret heartbeat success as a full-store scan.

## Durable lifecycle telemetry (not scanner evidence)

- Windows database: `%LOCALAPPDATA%\CreativeAssetManager\BrandingDNA\<agent-id>\worker-outbox.sqlite`. On Linux QA hosts, `$XDG_STATE_HOME` or `~/.local/state`. Dedicated per-agent directory, symlink file rejection.
- Local SQLite WAL + `synchronous=FULL` writes; each lifecycle event is committed before delivery and deleted only after an **exact complete ACK** from CAM. Max 2,000 pending rows, batch up to 50; outbox-full fails closed without discarding old events.
- Accepted events are `started`, `stopped`, `heartbeat_failure`, `heartbeat_recovered` with fixed detail codes. No arbitrary messages, credentials, shop URLs, cookies, images, or user content can be sent.
- Server event endpoint `POST /api/v1/branding-dna/workers/{agent_id}/events` authenticates the existing agent Bearer token, tenant-scopes events, handles replay via event ID + digest, rejects conflicting duplicates, limits new events to 1,000 per agent/day and accepts timestamps at most 30 days old.
- Connectivity failures and invalid ACKs leave rows pending. HTTP redirect following disabled and 15s request timeout. Revoked enrollment fails closed.
- **Do not confuse this lifecycle outbox with the future per-task result outbox**. No scanner jobs are dispatched. Production database migration and Windows installer/update have not been performed. Node's built-in SQLite carries an experimental warning on Node 22 and requires a pinned runtime QA pass.

## Required upgrades for scanning

Tenant-bound lease-to-worker dispatch, versioned parser adapters, per-shop profile lock, limited gallery evidence, CAPTCHA/manual blocker states, signed upload and controls to stop/pause. The local SQLite **lifecycle** outbox is now implemented, but task/result outbox and verified marketplace adapters are not. See `docs/features/branding-dna/WORKER_ARCHITECTURE.md`.
