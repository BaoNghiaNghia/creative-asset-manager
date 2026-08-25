# Inventory Gemini Agent V4.1 handoff

## Current engineering state

The V4.1 application path supports real Google reads, native Gemini tool calls, evidence-backed staging, server-configured auto execution, stale-evidence revalidation, and read-back verification. Inventory AI cost reservations are preserved when Gemini does not report an actual provider cost. Application lifespan uses the exact Settings instance that created the app.

This document does not claim a production activation. Confirm the production state through the runbook before a live write.

## Operator prerequisites

- Inventory automation and dedicated Inventory AI are enabled only for the intended tenant.
- The tenant has an active dedicated Inventory Gemini credential and authorized Google connection.
- V4 configuration resolves one working spreadsheet and explicit allowed sheets.
- apply_mode is auto only after a controlled manual run is approved.
- Formula, merged-cell, protected-range, evidence, material, and operation limits remain at their configured safety values.
- The current production workbook and allowed sheet are obtained from configuration; never trust a stale handoff ID.

## Rollback

Before configuration changes, save a timestamped, mode-600 backup of /etc/creative-asset-manager/production.env; record active release, Alembic revision, current tenant setting, scheduler state, V4 apply mode, and working workbook ID without exposing secrets.

If a manual run blocks or fails, leave the scheduler disabled. Roll back application activation with the established backend release rollback and restore the previous Inventory configuration only after reviewing the safe run metadata. A partial execution report must identify verified writes and any non-executed operations.
