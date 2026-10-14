# RRUGC Codex Account Manager — limited first release

## Production scope

The Stage 4 header has a **Codex accounts** button opening a responsive
admin modal. This release supports **two independently authenticated Codex
profiles**, read-only CLI authentication status, tenant-scoped saved
preferences (name, requested concurrency, stages, enabled state), and
authorization using platform administrator or `tenant_members.manage`.
Changes are recorded in `processing_policy_audits`. No credentials or CLI
output are stored in the database or returned to the browser.

**Routing remains single active account.** Existing Stage 1, Stage 2 and
Stage 4 workers continue to use the existing `CODEX_IMAGE_HOME`, regardless
of the preference toggles. A second profile does not become a worker until a
separately reviewed, permission-compliant scheduler is implemented. UI and API
expressly disclose this limit; do not describe preferences as currently
enforced parallel job slots.

## Provisioning

The original Codex runtime remains under
`/var/lib/creative-asset-manager/codex`. The second session is a distinct
`/var/lib/creative-asset-manager/codex-account-2`. It must never copy
`auth.json` from Account 01. Skills are shared using a managed symlink
pointing to the existing original skills directory.

On the VPS, as an administrator:

```bash
cd /root/creative-asset-manager
sudo bash scripts/cam-codex-runtime.sh prepare-2
sudo bash scripts/cam-codex-runtime.sh login-2
sudo bash scripts/cam-codex-runtime.sh status-2
```

Complete the device authentication interactively. The account owner must
authorize the use of the corresponding identity and its workload.

Then go to AI Operations → Reviews → Stage 4 → **Codex accounts**, click
**Check status**, configure and save account settings. Only tenant
administrators can save; others may see read-only status. Never paste a
password, token, device code or auth.json into the web UI or logs.

## Safety and next phase

Do not merge multiple subscription allowances to bypass per-user quotas,
automatically change identity on quota/rate-limit errors, or transfer session
files between accounts. To scale throughput, consider an appropriate API or
business capacity agreement. A future scheduler must explicitly enforce
per-account limits, permission policy, job leases, heartbeat, cooldown and
restart recovery; implement only after verifying allowed usage.

## Verification

Backend: `test_codex_account_manager.py`, migration head check.
Frontend: `CodexAccountManager.test.tsx`, Stage 4 QA fixture and all
responsive Browser QA viewports. The modal never claims the secondary
profile is currently processing jobs.
