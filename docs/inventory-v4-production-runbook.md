# Inventory Daily Sheet V4.1 production runbook

## Scope

Use this runbook for the first live V4.1 run. V4 uses Gemini native tools for semantics and the application V4 executor for mechanical safety. Do not use legacy V2 snapshot/reset or V3 plan/apply paths for a V4 tenant.

## Preflight

1. Deploy a tested, committed release using scripts/cam-rebuild-backend.sh.
2. Check API, Inventory worker, and Inventory scheduler health using the installed service units.
3. Record active release and Alembic revision.
4. Back up /etc/creative-asset-manager/production.env with owner root, group root, and mode 0600; do not print it.
5. Resolve the Inventory tenant, working spreadsheet ID, allowed sheets, V4 config version, and apply mode from the production database/configuration.
6. Verify Google connection and dedicated Inventory Gemini credential without printing tokens or keys.
7. Run configuration validation. It must prove workbook authorization, allowed sheets, metadata access, and V4 limits.
8. Keep daily_sheet_automation_enabled=false until the controlled manual run succeeds.

## Controlled manual V4 run

1. Confirm the configuration is V4 with agent.apply_mode=auto.
2. Invoke the normal authenticated V4 route or service path once for the selected business date. Do not use an ad-hoc Google write script.
3. Record only safe summary fields: run ID, workbook ID, allowed sheet names, Gemini model, tool rounds, tools called, assessment state, read cells, plan hash, planned operation count, executed operation count, review skips, and verification result.
4. Verify that no staged operation is blocked or review-required before any write.
5. Confirm every target written by the executor matches read-back results.
6. Confirm any clear occurred only after its SET read-back verification.
7. If anything fails or is ambiguous, stop. Do not retry writes until the current workbook state and idempotency are reviewed.

## Scheduler activation

After one manual run reports status=completed and verified writes:

1. Set V4 agent.apply_mode=auto and daily_sheet_automation_enabled=true through the supported configuration path.
2. Validate configuration again.
3. Confirm the scheduler uses run_agent_v4, not snapshot_and_reset or legacy reconciliation.
4. Verify timezone and configured snapshot cadence. The scheduler suppresses repeat successful V4 runs for a tenant/date in its process and retries non-completed results.
5. Check Inventory scheduler logs for safe run metadata and health.

## Failure and rollback

- Stale evidence, safety validation, assessment, material, or read-back errors are fail-closed.
- Do not enable the scheduler after a manual failure.
- Report partial writes exactly with their verification state.
- Use the normal immutable release rollback and restore the previous Inventory configuration when appropriate. Do not attempt a manual compensating workbook mutation without a reviewed operational procedure.
