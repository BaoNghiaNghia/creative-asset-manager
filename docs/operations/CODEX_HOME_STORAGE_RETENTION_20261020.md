# Codex HOME storage inspection and retention — 2026-10-20

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
generated PNGs, models, databases and production releases remain untouched.
