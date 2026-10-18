# CAM VPS hardening — actionable execution plan (2026-10-18)

**Scope:** production VPS `creativeasset` (Ubuntu 24.04, 50 GiB system volume), Creative Asset Manager and Scout Manager.
**Mode:** safety-gated, no production-data deletion, no automatic privileged network changes.
**Source branch:** `feat/branding-dna-visible-windows-agent-20261017`; deployments are independent from maintenance.
**Release policy:** keep protected current/previous backend and frontend releases, live processes' releases, all database backups, Elasticsearch data, R2 credentials and asset data.

## Verified baseline (18 October, 16:49–16:36 ICT observations)

| Signal | Observed | Interpretation |
| --- | --- | --- |
| /live + /ready | Healthy (PostgreSQL, Elasticsearch) | Do not restart services just for cleanup |
| Disk | 50 GiB, 9.9 GiB free, 79% used; inodes 19% | Healthy headroom, but future release needs 2 GiB floor plus one build |
| Memory | 7.8 GiB physical, 4.2 GiB available; 1.3/2 GiB swap used | Monitor actual memory pressure before adding workers |
| systemd | networkd-wait-online.service failed | Persistent boot diagnostics; **not** proof network is currently offline |
| Netplan eth0 | IPv4 default via provider; static IPv6 `2401:3660:0:11ed::1/64` with off-subnet configured `2401:3660::1` gateway | No IPv6 default route; provider verification required |
| Network setup | eth0 `routable (configuring)`, `systemd-resolved` inactive | Diagnose networkd waiting; do not blanket-disable wait-online |
| Database backups | Tue/Fri 22:00 timer active; last service success Fri 2026-10-16 | **Restore not demonstrated**; NOT sufficient for DB-changing releases |
| Docker | Elasticsearch image active + data volume (3.782 GB); PostgreSQL 16.4 image 617 MB unused **by currently running containers** | Preserve integration-test image until dependency reviewed; NEVER prune volumes |
| Journals | 510 MB before / 196.5 MB after vacuum | 321.4 MB archived journals reclaimed |
| Scout releases | Kept SHA256-verified 0.1.28; removed old 0.1.21–0.1.27 | 101.3 MiB reclaimed; no public Git files changed |
| Browser QA | Four orphaned loopback Vite preview servers stopped; temporary reports pruned to latest 5+3 | Recurrence fix under tests; failed/baseline evidence should remain protected |
| HTTPS certificate | Valid through 18 November 2026 | Monitor certbot renewals |
| Git working tree | Clean before this maintenance patch | No production release activation during maintenance |

## Execution matrix and order of operations

### P0 — establish a recoverable production baseline

1. **Read-only operator inventory.** From a trusted operator shell (not CodeLocal workspace policy bypass), inspect:
   `readlink -f /opt/creative-asset-manager/current` and `previous`;
   `readlink -f /var/www/creative-asset-manager/current` and `previous`;
   `systemctl show ... -p MainPID`, the running process CWD and the release symlinks.
   Capture a manifest containing commit hashes, directory sizes, service status and the active Alembic revision.
2. **Backup proof.** Check `creative-asset-manager-db-backup.timer`, latest successful service log and configuration using the authorized production identity. Run `database_backup_cli verify-config` and `list` *inside the operator-authorized runtime*. Capture the newest managed Drive file ID, time, exact size, remote MD5, appProperty SHA-256 and database name **privately**. Timer success alone is not proof of the remote file or restorability.
3. **Independent restore rehearsal.** Download the selected backup into a secure staging location, verify checksums against the managed remote metadata and `pg_restore --list`; restore to an isolated, empty PostgreSQL instance/database under a dedicated nonproduction credential. Check that the schema, Alembic revision and representative table counts are reasonable. Produce a dated `restore=PASS` record with dump SHA-256, target isolation, database version, operator identity and duration. Never log database contents or any credential. Clean the disposable DB only after proof has been captured.
4. **Release gating.** If the restore or signature/identity checks fail or evidence is missing, **STOP** backend migration/deploy and retain current release. The current authorized CodeLocal checkout cannot access production `/opt`, `/etc` or backup storage directly. A trusted operator or separately approved, narrowly scoped production tool must perform those checks; do not source protected env files into logs/chat.

**Pass:** a real restore proof associated with a current verified backup, one Alembic head, and both release pointers captured. **Rollback:** no production mutation occurs during preflight.

### P1 — repair network wait-online safely

1. **Already observed:** `eth0` is routable over IPv4 but `networkctl` calls it configuring; no IPv6 default route. Netplan's configured IPv6 gateway is outside the local `/64`. Generated wait-online unit contains two sequential `ExecStart` checks. `systemd-resolved` is inactive; a `resolvectl` error alone is not proof DNS requests fail.
2. **Provider verification:** confirm the actual IPv6 gateway, whether off-link next-hop with `on-link: true` is required, and whether IPv6 gateway routing is enabled. Never infer or fabricate the gateway.
3. **Operator-only canary:** ensure provider serial/emergency console and a verified backup of the existing Netplan YAML. Create the minimal provider-approved route change; validate via `netplan generate`, and apply with `netplan try --timeout 120` **from the emergency console**. Do not restart `systemd-networkd` or apply changes over the only SSH channel.
4. Confirm SSH on port 6709, IPv4 default route, IPv6 default route and gateway reachability, DNS lookups, `networkctl status eth0`, `systemd-networkd-wait-online` and external /ready. Reboot validation only in an agreed maintenance window. Clear the old failed unit **after** the cause is fixed.
5. If the provider does not authorize/confirm IPv6, preserve working IPv4, document the disabled IPv6 route as a separate reviewed change, and leave the warning visible rather than hiding it via `optional:true`, force-units, or reset-failed alone.

**Pass:** stable IPv4 + IPv6 and the wait-online check succeeds on the next boot. **Rollback:** provider-console restoration of the exact saved Netplan file.

### P1 — prune genuinely obsolete artifacts, not project data

1. **Backend releases:** only use the audited `cleanup_old_releases` logic in `scripts/cam-rebuild-backend.sh` **after** recording current/previous and all running workers' CWDs. Keep `current`, `previous`, the candidate release and any process-pinned release. The deployment script can prune during a later approved deployment, but do **not** run a deployment merely to trigger cleanup.
2. **Frontend releases:** keep both `current` and `previous`; if any release is served by Nginx or a pending rollback, keep it. Dry-run and record complete eligible sizes before operator-approved deletion.
3. **QA/preview:** prevent orphaning by launching Vite directly as the process-group leader; `scripts/cam-clean-orphaned-ui-qa.py` defaults to a dry run, and `--apply` rechecks PID, PPID, bounded age, exact command, loopback and repo-local cwd. Never target production ports or non-orphan processes.
4. **Browser evidence:** `scripts/cam-clean-browser-qa.py --dry-run` should be the preferred retention cleaner. Preserve failed runs (14 days), pending baseline proposals, authenticated production smoke evidence, reference screenshots and recent runs. Do not delete entire `.ui-qa` or an accepted baseline.
5. **Other data:** preserve Docker Elasticsearch container, attached volume and image; preserve PostgreSQL 16 integration-test image until CI dependency audit; preserve `.codelocal/pod-audit` and managed Drive/Google files until their owner confirms retention. Never use `docker system prune --volumes`, broad `rm -rf`, `journalctl --vacuum-time` without cap, or destructive DB migrations.

**Pass:** disk utilization improves with a named file manifest; /live, /ready, /version, workers, and queues remain healthy. **Rollback:** do not delete protected backups or release pointers; restore removed disposable build artifacts by rebuilding from Git/CI only.

### P2 — maintenance controls and regression protection

1. Keep journals near 200–250 MiB with a reviewed journald policy; avoid shortening security audit retention to zero.
2. Disk alerts: warn at ≥80% used or <8 GiB available; critical at ≥90% or <5 GiB. Monitor free inodes, PostgreSQL growth, Elasticsearch volume and pending backup staging files.
3. Memory alerts: investigate sustained `MemAvailable <2 GiB`, swap growth (currently 1.3/2 GiB), OOM kills and worker restarts; do not delete swap/restart workers based on a single snapshot.
4. Mesa packages: eight pending updates; stage `apt` maintenance separately. Do not run unreviewed `apt upgrade` while browser QA, Dola/Xvfb or image workers are processing.
5. Add CI for orphan preview recognition, PID reuse and wrong-cwd rejection, and a live loopback start→stop smoke to prevent future leaks. Require GitHub CI and immutable build provenance for future production releases.

## Final validation and evidence to retain

```bash
systemctl --failed --no-pager
systemctl is-active nginx postgresql docker creative-asset-manager-api
pg_isready
docker ps
df -h
df -i
free -h
journalctl --disk-usage
curl -fsS https://creative-assets.ddns.net/live
curl -fsS https://creative-assets.ddns.net/ready
curl -fsS https://creative-assets.ddns.net/version
python3 scripts/cam-clean-orphaned-ui-qa.py   # dry-run only
python3 scripts/cam-clean-browser-qa.py --dry-run
```

**Not done / not claimed:** production backup download/restore, production release deletion, Netplan modification, Mesa OS upgrade, production deployment or migration. Those require the separately approved operator boundary and the gates above.
