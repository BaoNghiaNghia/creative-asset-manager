# Branding DNA Windows Worker (control-plane bootstrap)

This program currently sends signed **heartbeats only**, with a 60-second interval and capped backoff. It does not navigate the web, crawl shop pages, fetch products, analyze images or lease real jobs. Its purpose is to prove the CAM-to-Windows worker connection before adding authorized source adapters.

## Setup (on an authorized dedicated Windows machine)

1. Install supported Node.js LTS (>=20) and copy this worker directory into its own Windows directory. Avoid using Pinterest Scout's Chrome profile or process.
2. In CAM Branding DNA → Dedicated Windows Worker, register an agent. The API shows an **agent ID and token once**. Store them in an OS-protected secret vault. Do not commit or log the token.
3. Inject the three environment variables only into the worker process: `BRANDING_DNA_SERVER_URL`, `BRANDING_DNA_AGENT_ID`, `BRANDING_DNA_AGENT_TOKEN`. Do not place secrets in the .cmd, repository or public configuration.
4. Run `node --test tests/*.test.mjs`, then `node src/heartbeat.mjs` or the .cmd. CAM should show heartbeat/online within ~1 minute. To stop immediately revoke the token via Disable in CAM.

## What is not installed/enabled

There is **no Playwright/browser binary dependency yet** in this control-plane package. Pin and install Playwright in the isolated worker during the authorized adapter phase after platform permissions and task leases are implemented. Do not interpret heartbeat success as a full-store scan.

## Required upgrades for scanning

Tenant-bound lease fencing, local SQLite outbox, versioned parser adapters, per-shop profile lock, limited gallery evidence, CAPTCHA/manual blocker states, signed upload and controls to stop/pause. See `docs/features/branding-dna/WORKER_ARCHITECTURE.md`.
