# Codex HOME storage inspection and retention — 2026-10-20

**Updated final-only policy:** See
[`CODEX_FINAL_ONLY_DRIVE_POLICY_20261020.md`](CODEX_FINAL_ONLY_DRIVE_POLICY_20261020.md).
The legacy recovery archive previously uploaded all unclassified cache PNGs.
That is no longer acceptable for ordinary jobs: automatic unclassified
archiving is now disabled. Only actual final outputs may be persisted on
Managed Drive. Review/QA/draft images must remain temporary, not archival
assets. The historical sections below document past operations, not the
current desired automatic archive behavior.

## Verified on creativeasset

Two Codex homes are intentionally separate to preserve independent logged-in
accounts: `/var/lib/creative-asset-manager/codex` and `codex-account-2`.
The second home shares the managed `skills` folder by symlink. Both homes
contain a tiny `auth.json` (~4 KiB) that must NEVER be reset while preserving
Google/Codex authorization.

The Codex generation provider executes `codex exec --ephemeral`, so it is
not intended to persist per-job conversation transcripts. However the Codex
image tool creates local PNGs under `CODEX_HOME/generated_images`, outside
the stage workspace cleaned by `CodexImageProvider.cleanup_attempt`.

Read-only metadata audit at 2026-10-20:
- Primary generated_images: ~2.0 GiB, 1,083 PNG files.
- Secondary generated_images: ~2.2 GiB, 1,002 PNG files.
- Combined local PNG duplicates/candidates: ~4.2 GiB. Their successful Drive
  upload is **not proven** merely by their age, file type, or location.
- At initial audit, secondary HOME/tmp: ~55k 0-byte files and ~55k dirs,
  ~217 MiB allocated mainly in directory metadata.
- Both homes had one old root-owned `tmp/arg0/codex-arg0*` directory and
  root-owned `.lock` inside it. `strace` of `codex login status` confirmed
  the exact `.lock` caused `EACCES` for service user `creative-assets`.
  A guarded, owner-only metadata repair of these two stale paths restored
  Codex's own temp cleanup. After running login status, both sessions still
  reported "Logged in using ChatGPT" without warnings, and secondary tmp
  had only four directories (down from 55k+); available disk rose from
  7.7 GiB to 8.7 GiB in rounded `df -h` output. No session or PNG deletion
  was performed by the repair script.
- Skills/plugins/cache/model metadata may be required by Codex and must not be
  indiscriminately deleted. SQLite files and WAL/SHM are live state.
- Session/auth data is tiny compared with retained images. The requirement
  "keep only Codex session" should be implemented as **no durable media in
  Codex HOME after remote-verified output**, NOT by wiping the HOME.

## Safe operating instructions

```bash
cd /root/creative-asset-manager
python3 .worktrees/branding-dna-dispatch/scripts/cam-codex-storage-report.py
python3 .worktrees/branding-dna-dispatch/scripts/cam-codex-tmp-permissions.py
bash .worktrees/branding-dna-dispatch/scripts/cam-codex-tmp-owner-fix.sh --dry-run
python3 .worktrees/branding-dna-dispatch/scripts/cam-codex-tmp-cleanup.py --older-hours 48
bash .worktrees/branding-dna-dispatch/scripts/cam-safe-disk-cleanup.sh --dry-run
```

The temp cleaner is intentionally dry-run by default and, even in --apply,
deletes only stale zero-length files and empty dirs in the two exact
`CODEX_HOME/tmp` directories, and refuses to run while a Codex process
exists. It never touches PNGs, credentials, auth, SQLite, or sessions.

The general disk cleanup script retains the active immutable release and an
older fallback; its `--apply` is independent of Codex retention. Production
pip cache can be purged with the active release virtualenv and service user,
but should not be purged during a deploy.

## Required pipeline change before automatic PNG removal

1. When a Codex image job starts, record a **per-job manifest** of local
   generated PNG source paths with inode/hash, account/home identifier and
   generation ID. Do not infer ownership from a broad timestamp range when
   concurrent workers share a home.
2. Upload final images to Managed Drive via the existing storage adapter.
   On each successful upload, persist tenant, job, SHA-256, remote_file_id,
   remote_folder_id and completion status transactionally.
3. Verify remote object existence and expected checksum/content length where
   provider supports it, using the actual configured Drive client. If the
   verification is unavailable or fails, keep the local candidate and mark
   `verification_pending`. Multi-output jobs require per-asset evidence.
4. Only after all required outputs for that job are remotely verified and
   retry/checkpoint policy permits finalization, delete **those exact**
   recorded local PNG paths under the intended generated_images root. Recheck
   path ownership/inode/hash and that no active job still needs them.
5. Record deletion audit (job ID/hash/bytes only), use a retention grace
   window, bound batch size, and expose retry+alert on failed verification.
   Never upload or delete local working assets under another account or tenant.

Do not delete historical PNGs by age alone: existing files have no proven
one-to-one mapping to Drive objects. Backfill/verify their provenance first
or retain them.

## Other identified growth

- 2 obsolete releases: ~815 MB; current and newest fallback remain protected.
- CAM pip download cache: ~281 MB, rebuildable.
- syslog + rotated syslog: ~663 MB, root cause and logrotate policy still
  need review; do not truncate an active log file.
- Active PostgreSQL, Elasticsearch volumes, model weights and the 2 GB swap
  file must remain untouched.

The temp-owner repair changed only directory/lock ownership and mode;
Codex itself subsequently reclaimed its abandoned temp trees. Session tokens,
generated PNGs, models, databases and production releases remain untouched
**by that particular repair**.

## Verified local-image cleanup (2026-10-20)

Two obsolete immutable releases (814 MiB) and the CAM pip cache (281 MiB)
were safely deleted using `cam-safe-disk-cleanup.sh --apply`. This preserved
the running release and the newest offline rollback candidate.

`scripts/cam-codex-drive-reconcile.py` now performs a source-of-truth check:
it reads completed CAM database records, computes the local PNG SHA-256 and
MD5 without exposing file contents, then checks **the CAM Managed Drive**
file's tenant appProperty, SHA-256 appProperty, size and Google-reported MD5.
It never deletes a local image without that exact remote evidence. A local
file's inode, modification time and both checksums are rechecked immediately
before unlink, and young images are protected for at least four hours.

First full run: 2,119 local PNGs, 913 distinct completed Drive records,
495 metadata candidates and 493 verified Drive copies. Applying with a
four-hour grace window deleted 479 verified PNGs (~1002 MiB allocated);
14 recent verified files and 1,628 unverified files remained intact.
Numbers vary as workers continue generating new images.

Commands:

```bash
# Full metadata check, read-only:
sudo /opt/creative-asset-manager/current/apps/api/.venv/bin/python \
  /opt/creative-asset-manager/current/scripts/cam-codex-drive-reconcile.py --max-drive-requests 3000
# Verified-only cleanup; never delete unverifiable originals:
sudo /opt/creative-asset-manager/current/apps/api/.venv/bin/python \
  /opt/creative-asset-manager/current/scripts/cam-codex-drive-reconcile.py --apply --min-age-hours 4
```

`deploy/systemd/creative-asset-manager-codex-generated-image-gc.service`
and `.timer` define an hourly verified-only cleanup (actual active timer
schedule is determined by the installed systemd unit).
They must be deployed from an immutable release and enabled only after
production smoke checks; they do not archive images missing job evidence.

Historical Codex PNGs without a matching completed CAM output record must
never be deleted by age alone. The separate
`scripts/cam-codex-drive-archive.py` can archive them into a designated
private Managed Drive recovery folder, verify each uploaded file's MD5,
SHA-256, size and metadata, then remove the exact original. Its first
dry-run and live one-file archive/upload/readback both passed on
2026-10-20. A controlled backlog migration is now running in bounded
batches with per-file remote verification. Failed uploads leave the local PNG
untouched; retries are bounded and concurrent Drive operations are capped.
`deploy/systemd/creative-asset-manager-codex-drive-archive.service` and
`.timer` provide the same safe reconciliation on a periodic basis when
installed and enabled by the host administrator. The target cadence is
hourly: run database-backed verified-image GC near :05, then archive
remaining local generated PNGs near :35. Each archive requires full Drive
readback with SHA-256/MD5 and a four-hour grace period for in-flight jobs.
As of 2026-10-20 13:25 Asia/Ho_Chi_Minh, the archive timer was enabled,
and a manual start of the oneshot service succeeded: 55 Drive-verified PNGs
were removed from VPS (122 MiB), with 0 failed uploads. The service will
show `inactive (dead)` after finishing successfully; this is normal for a
`Type=oneshot` unit. Do not confuse an enabled timer with a successful
service invocation: always inspect `journalctl`.
The archive is distinct from a product's official output folder; unknown
historical media cannot be retroactively assigned to a tenant/shop from
timestamps alone.

### Running the archive correctly on the production VPS

The script lives in the **immutable deployed release**, NOT the root checkout
`/root/creative-asset-manager/scripts`. In particular, invoking the release
Python with a *relative script path* from the root checkout yields
`can't open file: ... No such file or directory` even when the timer is healthy.

```bash
# Recommended: run through the installed systemd service sandbox.
sudo systemctl start creative-asset-manager-codex-drive-archive.service
sudo journalctl -u creative-asset-manager-codex-drive-archive.service -n 50 --no-pager

# Or invoke the correct absolute release script path:
sudo /opt/creative-asset-manager/current/apps/api/.venv/bin/python \
  /opt/creative-asset-manager/current/scripts/cam-codex-drive-archive.py \
  --apply --max-files 500 --min-age-hours 4 --concurrency 5

# Timer is enabled independently from the oneshot service:
systemctl list-timers --all | grep codex
```

The four-hour grace period intentionally allows newly generated PNGs to remain
temporarily until verified remote archival on a later run.

**Operational goal:** sessions/auth and necessary CLI runtime live in
`CODEX_HOME`; generated PNGs are transient and durable output is on
Managed Google Drive. Never delete a failed upload or image pending
reconciliation. Never claim the legacy backlog is all on Drive until
every remaining file has matching remote proof or a verified archive copy.
