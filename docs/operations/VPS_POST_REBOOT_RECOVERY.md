# Post-reboot VPS recovery — Ubuntu 6.8.0-146

This note covers the 2026-10-11 maintenance boot on Creative Asset Manager's production VPS.

## Confirmed state

- Kernel upgrade to `6.8.0-146-generic` succeeded.
- PostgreSQL, API, seven image/general workers, Dola gateway and visual encoder have running processes.
- Docker Elasticsearch is healthy.
- Browser smoke (public-only) passed 6/6 routes, with zero errors or warnings.
- The older `creative-asset-manager-worker.service` is inactive, but specialized image workers are actively claiming jobs. Do **not** enable the old service merely to remove an `inactive` line.
- Active swap is `/swapfile-camqa`, 2 GiB.
- Obsolete `/swapfile` in `/etc/fstab` has an invalid swap header, causing `swapfile.swap` to fail.
- `eth0` has working IPv4 and is `routable (configuring)`; `systemd-networkd-wait-online` times out after 120 seconds.
- Netplan config has IPv6 `2401:3660:0:11ed::1/64` and a default gateway `2401:3660::1` that is **outside that prefix**. There is currently no installed IPv6 default route. This warrants provider verification; the missing default route alone does not prove that this is the only reason for the networkd configuring state.

## Audited, local swap repair

**Operator command (requires root, not applied automatically by CodeLocal):**

```bash
cd /root/creative-asset-manager
python3 scripts/cam-vps-post-reboot-repair.py --audit
sudo python3 scripts/cam-vps-post-reboot-repair.py --apply-swap
swapon --show
findmnt --fstab --types swap
sudo systemctl --failed
```

The apply command refuses changes unless the active 2 GiB CAM swap is verified and present in fstab. It comments out *only* the exact `/swapfile` swap entry. It keeps an unmodified backup with prefix `/etc/fstab.cam-pre-swap-`, applies the rewrite atomically, runs `systemctl daemon-reload`, and tries to clear the obsolete `swapfile.swap` failed state. Re-running is idempotent. No swap activation/deactivation and no networking changes occur.

**Rollback:** identify the exact saved backup named in the tool output, then (as root) `cp -a BACKUP_PATH /etc/fstab` and `systemctl daemon-reload`. Only rollback if needed; restoring the invalid old swap entry would reintroduce the boot warning.

## Network wait-online — do not change SSH-connected routes blindly

Do not disable `systemd-networkd`, restart networking unguarded, remove public IPv4, or invent an IPv6 gateway. A `netplan apply` over the current SSH port `6709` may temporarily or permanently disconnect the operator.

Before changing IPv6 route flags or readiness policy:

1. Confirm with the VPS provider whether `2401:3660::1` is intentionally **off-link** and requires a route with `on-link: true`, or whether a different gateway is provided.
2. Keep a working provider emergency console available. Back up the existing Netplan YAML and run `netplan generate` to validate syntax; apply the corrected configuration using `netplan try --timeout 120` from the provider console or another recovery-safe session. Confirm IPv4 SSH, IPv6 gateway connectivity, `ip -6 route show default`, `networkctl status eth0`, and the next boot's wait-online behavior.
3. Only after network configuration really reaches a ready state, clear the previous failed unit with `systemctl reset-failed systemd-networkd-wait-online.service` and inspect `systemctl --failed`.

Setting `optional: true` on the public interface or overriding the wait-online unit merely to hide the failure is **not** applied as a default remediation: it changes boot readiness semantics for all dependent services. Fix the network cause before suppressing diagnostics.

## QA verification after the operator swap fix

```bash
uname -r
swapon --show
findmnt --fstab --types swap
pg_isready -h 127.0.0.1 -p 5432
docker ps
df -h /
CAM_PRODUCTION_UI_MODE=public make production-ui-smoke
```

Production public Browser QA cannot prove authenticated private pages or active queues are healthy. Backup and worker readiness checks remain independent.
