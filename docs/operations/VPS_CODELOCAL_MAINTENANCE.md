# CodeLocal Full Access + scoped production VPS maintenance

**Decision, 2026-10-11:** CodeLocal Full Access is the persistent default **for the Creative Asset Management workspace**, covering normal code changes, review, tests, commits, pushes, and user-authorized deployments. This is not unrestricted operating-system/root access.

**Important distinction:** Today `node /usr/bin/codelocal` on the VPS runs as **root**, but the CodeLocal **host-policy** still blocks `sudo`, writes outside its workspace and certain privileged operations. Merely selecting "Full" does **not** bypass these hard blocks or create least-privilege execution. An actual limited-privilege setup requires a dedicated non-root CodeLocal runtime plus explicitly approved access to the maintenance helper.

## Broker design

`scripts/cam-vps-maintenance.py` is a **fixed-command** root-owned program, intended to be installed at `/usr/local/sbin/cam-vps-maintenance`. The installed code must not point back to a writable Git checkout. `scripts/cam-install-vps-maintenance.sh` installs a root-owned copy and a `visudo`-validated **exact-argument sudoers policy** for one dedicated non-root operator, **not** the `creative-assets` application account.

The broker permits **only**:

| Action | Scope | Audit/rollback |
|---|---|---|
| `status` | Read kernel, active swap, fstab swap entries, failed systemd units, PostgreSQL readiness, disk | Audit |
| `network-audit` | Read `networkctl` and IPv4/IPv6 default routes; never modify network | Audit |
| `swap-repair` | Disable only the obsolete `/swapfile` fstab entry, after verifying `/swapfile-camqa` is active and persisted | Backup, exact rollback, audit |
| `swap-rollback` | Restore original fstab **only if it still exactly matches the broker's edit** and the saved backup hash is valid | Audit; will restore prior warning |
| `stage1-retention-retry` | Start the fixed Stage 1 temp retention systemd service; existing 48-hour, folder, ownership and file recheck guards remain | Audit; delete operation itself is not reversible |

The allowlist intentionally excludes arbitrary shell/Python, apt/package operations, unattended reboot, SSH/firewall/DNS/Netplan modification, bulk file deletion, Docker pruning, database writes and secrets inspection. A future action needs a reviewed code change, negative tests and a **new root-controlled install**. "Full" in CodeLocal does not grant any additional sudoers commands.

Each invocation gets a host-level `flock` lock and a root-only JSONL audit entry at `/var/log/cam-vps-maintenance/audit.jsonl` (action, timestamp, outcome, original caller UID and exception class; **no credentials or arbitrary request payloads**). Swap backups/rollback markers are owned by root in `/var/lib/creative-asset-manager/maintenance`.

## Operator-controlled one-time provisioning

**Not executed from CodeLocal:** The CodeLocal host-policy prevents modifying `/etc/sudoers.d`, `/usr/local/sbin` and the systemd identity of the running CodeLocal service. These hard blocks must be respected, never worked around by invoking an unreviewed service.

On the **trusted VPS operator console**, after reviewing this code and choosing a dedicated non-root service account:

```bash
# Example: create a separate account only if not already present.
sudo useradd --system --user-group --create-home --home-dir /home/codelocal-ops --shell /usr/sbin/nologin codelocal-ops

cd /root/creative-asset-manager
bash scripts/cam-install-vps-maintenance.sh --check
sudo bash scripts/cam-install-vps-maintenance.sh --install --operator codelocal-ops

# Confirm exact restricted sudo rights. This should NOT list ALL or a shell.
sudo -l -U codelocal-ops
```

**This installation does not migrate the existing CodeLocal service.** The active service is currently root-owned and its checkout is inside `/root`, which is not accessible to a non-root account. Complete a **separate tested migration** to a root-independent checkout in `/srv` (or another reviewed path), pair the new agent under its own account rather than copying root account credentials, validate its workspace access and service startup, then stop the old root instance. Do not run duplicate agents against the same project without a migration plan. Never give the application service account sudo rights.

The command broker must be **exposed through an explicitly approved CodeLocal host/tool policy** before attempting invocation by an agent. A sudoers entry by itself cannot override a CodeLocal hard-block, including `sudo` and filesystem access restrictions. Until the approved tool integration is active, a human operator may run the helper; automated OS repair is **not yet enabled**.

## Example scoped operator actions

```bash
# Once migrated to the authorized, non-root operator account:
sudo -n /usr/local/sbin/cam-vps-maintenance status
sudo -n /usr/local/sbin/cam-vps-maintenance network-audit
sudo -n /usr/local/sbin/cam-vps-maintenance swap-repair
sudo -n /usr/local/sbin/cam-vps-maintenance stage1-retention-retry

# Only if explicitly needed after reviewing the backup:
sudo -n /usr/local/sbin/cam-vps-maintenance swap-rollback
```

The existing `scripts/cam-vps-post-reboot-repair.py` remains available to root operators as a narrowly scoped one-off fallback. Do not automatically change live IPv4/IPv6 gateway configuration merely to make `networkd-wait-online` stop reporting failed; obtain provider-confirmed routing and a working out-of-band console first.

## Security review, risks, rollback

- Privilege: the installed broker runs as root; the caller is an otherwise unprivileged dedicated CodeLocal account, with only exact program/argument sudo permission. If the agent remains root this security distinction does not hold.
- Safeguards: no dynamic commands, shells or arbitrary paths from user input; symlinks are rejected for fstab and backups; edit is atomic and keyed to exact fstab source; rollback checks full original SHA-256 and rejects independent operator edits.
- Known risk: `stage1-retention-retry` can delete eligible old temporary Drive drafts as part of its existing scheduled semantics. It is not an arbitrary file-deletion command; outputs/finals and non-owned files remain excluded.
- Credentials: no OAuth values, database secrets or environment files are accessed by the broker.
- Audit retention: root should install routine logrotate or equivalent policy for the JSONL file; don't allow unlimited log growth.
- Disable scoped rights immediately with `sudo rm /etc/sudoers.d/cam-vps-maintenance` followed by `sudo visudo -c`. This does not affect running production services.
- Roll back installed broker only from a previously reviewed file under `/var/backups/cam-vps-maintenance`, and verify `visudo` before restoring the corresponding policy. To revoke CodeLocal Full, use CodeLocal workspace access mode `smart` or `prompt`. The project decision may be revised on request.
- For the October 2026 pending swap/network warnings see [post-reboot recovery](VPS_POST_REBOOT_RECOVERY.md). Do not reboot just to clear a stale failed state.
