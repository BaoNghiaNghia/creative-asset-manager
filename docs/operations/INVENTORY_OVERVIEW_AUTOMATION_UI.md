# Inventory overview-only UI — 21 October 2026

## User-approved operating model

Inventory runs automatically in the backend according to existing configured
schedules, tenant/source permissions, signed audit records, retries and
reconciliation gates. **The browser is not the scheduler.** A dashboard refresh
must only read status, never submit job/replay/finalize actions.

Only **/inventory** is a primary Inventory user page. Old /inventory/*
bookmarks route to the same overview without retaining secondary pages.

## Overview information kept

- Automatic mode state: true only when the server reports enabled, configured
  and not disabled; do not infer successful background operation from a visible UI.
- One business-date card, processing status, recent reconciliation summary;
  three read-only Reset / Snapshot / Gemini reconciliation status cards.
- One optional source-workbook link and a read-only Refresh button; status
  refreshes once per minute while the page is mounted.
- Exception indicator and a collapsed list of backend blockers, without any
  automatic approval, hiding unresolved failures or presenting a green success
  banner for missing data.
- A **collapsed human-attestation drawer** on the *same overview*, exposing
  authorized actions only when explicitly selected: physical count, manager
  approval, transfer receipt, document review, and unresolved material review.
  It preserves backend role checks and source-hash validation.

## Removed from the ordinary Inventory navigation

- Second horizontal Inventory tab bar and duplicative workspace header.
- Sidebar Inventory expandable submenu for pipeline, knowledge, materials,
  prompts and configuration.
- Quick action buttons for Daily Inventory, raw materials and settings.
- Long historical pipeline grid with manual rerun / recovery / force actions.
- Standalone Inventory pages reachable from navigation or deep-link routing.
- Automatic mounting of technical configuration or human attestation forms.

Legacy React modules and backend routes are **not deleted** in this release:
existing historical data, authorized manual attestations and operational API
contracts are preserved. The human-attestation drawer renders only the chosen
form and hides the legacy subnavigation. The database is unchanged.

## What this release does not authorize

Automatic physical count attestation, batch expiry certification, waste
disposal proof, discrepancy approval, receiving a transfer, signing a shift,
forced finalization, unverified canonical inventory debits or uncontrolled
Google Sheets writes. These require the Q01–Q47 safeguards and independent
human evidence where applicable. Removing buttons does not alter the existing
scheduler; if server status reports disabled/missing configuration, the
dashboard must say so.

## Verification

- TypeScript check and focused Inventory/Navigation/Ai Operations unit tests.
- Full GitHub CI before backend/frontend release.
- Authenticated read-only production Browser QA on /inventory desktop+mobile.
- Health, backend service state and unchanged Alembic revision after release.
