# Inventory Operations v2: compact UI and Codex CLI migration

Status (2026-11-18): UI code implemented; Codex CLI **shadow-analysis pilot only**. Automatic evening reconciliation, morning carry-forward, and verified Google Sheets writeback are still handled by established Inventory V4/Gemini tool-host code. No production provider switch or historical data rename is implied.

## What the repository currently does

- Morning Reset → immutable Snapshot → Evening Reconciliation → verification/human attestation.
- InventoryDailySheetV4Service runs multiple Gemini function-calling rounds against V4WorkbookToolHost. This trusted backend enforces tenant scope, Google Sheet read budgets, evidence hashes, material catalog mappings, staging and optional validated writeback.
- GeminiCarryForwardPlanner has its own evidence-backed multi-round tool flow. Previous-day verified closing stock never authorizes direct editing of today's Sheet without backend checks.
- Existing DB fields and Drive filenames with gemini in their names are provenance and storage identifiers, not a reason to mislabel the runtime provider.

Replacing Gemini tool calling with one free-text Codex CLI command would lose these safety contracts. CLI login and subscription quota also differ from a Gemini API key.

## Implemented Phase A: bounded, read-only Codex pilot

Module: app.modules.inventory.ai.codex_cli_shadow

- Accepts only explicitly supplied business_date and at most 320 exact cell/hash/value evidence records; does not fetch Google Sheets itself.
- Calls Codex CLI through a dedicated non-root service user's ChatGPT login, with ephemeral mode, a read-only sandbox, a strict JSON response schema, bounded timeout, temporary working directory and minimal environment.
- Rejects any invented evidence hash, additional response fields, unrecognized codes or over-large output. Returns apply_mode=shadow, verification_status=review_required, writes=0; no Google Sheets, SQL, Dropbox, source asset or external workflow mutation is possible from this module.
- No fallback to a separately billed API key or administrator's login; production scheduled jobs do not call the pilot.
- Includes network-free mock tests to verify input limits, citations and command sandbox settings. Tests are NOT a live model quality or authentication proof.

CLI reference: https://learn.chatgpt.com/docs/non-interactive-mode and https://learn.chatgpt.com/docs/cli/reference.

## Authentication blocker

The Linux host includes codex-cli 0.159.3. The default creative-assets service HOME reported Not logged in during the 18 November check; the administrator's HOME reported ChatGPT login. Do not transfer credentials across identities or run Inventory jobs as root. A protected dedicated CODEX_HOME and an authorized ChatGPT sign-in for the worker identity are needed before the first live Codex trial. Authentication and usage limits require explicit preflight.

## Phase B: migration gates (NOT enabled)

1. Preflight a protected service account and separate Codex shadow-only capacity with no API-key fallback.
2. Build an authorized read-only workbook evidence exporter using existing Google connector and inventory catalogs; redact personal/secrets before sending limited cells.
3. Compare Codex output to verified historical frozen snapshots and Gemini results, tracking evidence citation accuracy, material matching, unknown units, hallucinations, latency, limits, and false positives. No promotion.
4. Add an allowlisted tool-call protocol matching V4WorkbookToolHost and CarryForwardToolHost, one bounded call per round. Reuse the same StagedEdits validation, operational limits and drift/read-back checks.
5. Add explicit tenant + deployment provider selection, initially shadow then review; auto writeback only after controlled evaluation and backend authorization.
6. Record provenance of the real provider. Avoid renaming legacy Gemini working copies or changing historical references. Failed Codex jobs stay review_required; never silently write via Gemini fallback.
7. Roll back by disabling the Codex canary without affecting Gemini provenance, source Sheet, snapshots, or existing manually approved data.

## Compact UI policy

Separate infrastructure health from current day's cycle status. Reduce three large cards + three large step cards into a slim KPI strip and a connected three-step timeline with optional details. Show only actual exceptions; keep manual work in a disclosure. History defaults to 8 visible days per page with explicit current-page filtering; mobile users see vertical cards rather than an oversized horizontal table. All recovery/replay permissions stay server-side.
