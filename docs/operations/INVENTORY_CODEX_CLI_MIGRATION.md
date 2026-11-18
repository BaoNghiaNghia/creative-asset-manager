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
- Calls Codex CLI under the existing non-root `creative-assets` service user, using the managed `CODEX_IMAGE_HOME` Account 01 session by default. Account 02 is selectable explicitly; never rotate automatically on quota or auth failures. Existing worker `slot-0.lock` is reused via nonblocking flock so Inventory cannot stampede Codex jobs already using this profile. No auth file is copied or created. Execution uses `--ignore-user-config`, ephemeral mode, read-only sandbox, strict JSON response schema, bounded timeout, temporary working directory and minimal environment.
- Rejects any invented evidence hash, additional response fields, unrecognized codes or over-large output. Returns apply_mode=shadow, verification_status=review_required, writes=0; no Google Sheets, SQL, Dropbox, source asset or external workflow mutation is possible from this module.
- No fallback to a separately billed API key or administrator's login; production scheduled jobs do not call the pilot.
- Includes network-free mock tests to verify input limits, citations and command sandbox settings. Tests are NOT a live model quality or authentication proof.

CLI reference: https://learn.chatgpt.com/docs/non-interactive-mode and https://learn.chatgpt.com/docs/cli/reference.

## Existing Codex account reuse (18 November 2026)

Codex CLI 0.159.3 is installed. The normal service HOME does not carry its login, but CAM already manages two authenticated profiles: `/var/lib/creative-asset-manager/codex` (Account 01) and `/var/lib/creative-asset-manager/codex-account-2` (Account 02). Both profile directories are owned by `creative-assets` (UID 999) and the official `cam-codex-runtime.sh status` / `status-2` actions returned **Logged in using ChatGPT** when run as `creative-assets`.

No further sign-in is needed, and no copying of root's auth file, login identity, cookie or credential is permitted. The Inventory operator CLI resolves the correct managed profile using `Settings.CODEX_IMAGE_HOME` and `account_home()`. It selects **Account 01 only** unless an operator passes `--account account-2`; it fails closed on busy profile or quota rather than hopping accounts.

Production operator commands (from the project root):

```sh
# Check existing identity, no model invocation:
bash scripts/cam-codex-runtime.sh status

# One bounded, synthetic, zero-write Codex inference under creative-assets:
bash scripts/cam-codex-runtime.sh inventory-smoke

# Real bounded evidence pilot requires an operator-owned JSON file and
# explicit shadow gate, executed under creative-assets (never root):
INVENTORY_CODEX_SHADOW_ENABLED=1   python -m app.operations.inventory_codex_shadow shadow   --account account-1 --evidence-file /path/to/preauthorized/evidence.json
```

The smoke action uses synthetic input only and outputs a success status without exposing account or Inventory records. The real evidence operation must be invoked by the designated service identity with an approved evidence export; it does not fetch Sheets, schedule recurring runs or promote results into writeback. Login is distinct from quota/usage capacity, so one successful smoke does not guarantee uninterrupted automation.

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
