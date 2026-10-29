# Typography Engine V2 — API-only Ideas/Style Lab canary

**Scope (29 October 2026):** after backend release `61e7da54c` and Alembic
`0189` are verified, expose the already-deployed Ideas Library and Style Lab
only to CAM's sole active `creative-assets` tenant. This is an opt-in
administrative step, **not** a general V2 release.

## Activation

Only run from a clean checkout matching `origin/main`. A root operator
executes the dry-run and confirms the tenant, current API release and database
revision before applying:

```bash
python3 scripts/cam_typography_v2_api_canary.py
python3 scripts/cam_typography_v2_api_canary.py --apply
```

Dry-run does not modify files, restart services, call AI or generate backups.
Apply creates only two owned files: root-only
`/etc/creative-asset-manager/typography-v2-api-canary.env` (0600), and
`/etc/systemd/system/creative-asset-manager-api.service.d/99-typography-v2-api-canary.conf`
(0644). It reloads systemd and restarts **API only**, verifying a real
`/ready` HTTP 200 with the production Trusted Host header. Existing workers
retain their own environment and jobs.

The override sets `TYPOGRAPHY_V2_ENABLED=true` and an explicit allowlist
containing **only** the active tenant ID, while explicitly retaining:
`TYPOGRAPHY_V2_PILOT_WORKER_ENABLED=false`,
`TYPOGRAPHY_V2_IMAGEGEN_PILOT_ENABLED=false`, and
`TYPOGRAPHY_V2_PILOT_ROLLBACK_VERIFIED=false`. No Auto Scout activation,
bulk ImageGen, Stage 1 reruns or new Stage 2 jobs.

With the appropriate authenticated permission, Stage 1 should now display the
`Typography Ideas` and `Style Lab` tabs. The `/typography/status`
capability probe is authenticated; reject unauthorized tenants. Test desktop
and mobile real-browser QA and reference permission flows separately. Do not
infer fully accepted G6.3 Output History/Stage 2 integration from this canary.

## Disable / rollback without affecting data

```bash
python3 scripts/cam_typography_v2_api_canary.py --disable
python3 scripts/cam_typography_v2_api_canary.py --disable --apply
```

The script removes only the *exact* originally written files and restarts only
the API, again checking readiness. No PostgreSQL downgrade or asset cleanup
is performed. If restart/health validation fails, the original scoped
configuration is restored. Production database remains `0189`.
**Important:** old backend `d4573f7e` expects database schema `0187`, so
a mere release-symlink rollback is not supported after the migration. Do not
execute `cam-rebuild-backend.sh --rollback` without first validating the
migration state and a compatible downgrade plan.

The newest `main` G6.3 read-only batch inspector is **not yet in the deployed
`61e7da54c` backend**. This canary exposes only API functions already
present in that release. A later immutable release build, full CI, and
authenticated end-to-end G6.3 Output History/Stage 2 QA remain outstanding.

## Security requirements

- Keep independent remote backup as already verified, but do not generate
  additional backups solely for this API flag toggle.
- Never copy tenant identifiers, passwords, OAuth credentials or API tokens
  into Git, logs or user-visible progress.
- Maintain protected backup-only systemd runtime release and previous
  backend release while resource pressure remains high.
- If drop-in files are already present or changed by another operator,
  fail closed. Do not overwrite or remove unknown configuration.
