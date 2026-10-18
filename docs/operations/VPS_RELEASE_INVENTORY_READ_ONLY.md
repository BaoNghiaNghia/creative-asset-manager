# CAM production release inventory — read-only safety gate

**Status (18 Oct 2026):** helper implemented and unit-tested. **No real production
/opt or /var/www release inventory performed by the restricted CodeLocal workspace.
No production release was deleted or activated.**

## Why this gate exists

Both backend and frontend have immutable releases. A release may be referenced by:
- the `current` symlink used by Nginx or systemd;
- the `previous` symlink needed for emergency UI or application rollback;
- a running process whose cwd or executable path resolves inside a different release.

Deleting any of those can break an in-flight worker or destroy rollback capability.
A healthy API does **not** prove older releases are safe to delete.

**Frontend deployment fix (source only, not yet production-deployed):** the
frontend deployment script now preserves `previous` and does not
automatically delete all other immutable frontend releases after success.
The `--rollback` branch swaps `current` and `previous` on success, so
an operator may reverse the rollback. If the Nginx reload fails during
rollback, the script attempts to restore the original active link.
The deployment activation path now requires a valid existing `current`
rollback anchor. It records the previous symlink state before activation,
restores **both** `current` and `previous` if Nginx reload or any HTTP /
optional UI post-activation smoke fails, and fails explicitly if rollback
itself fails. A first install without a rollback anchor needs a separate
reviewed bootstrap procedure. Do not interpret a successful HTTP GET as
database or end-to-end functional readiness.

The source-level regression suite exercises success, both classes of failure,
missing `previous`, invalid target, and reversible rollback with isolated
fake Nginx/systemd/HTTP commands; no real deployment is performed by those
tests. A real operator must still verify the production release layout
before activation.

## Operator-only read-only audit

From a trusted operator console with expressly authorized read access to the
production directories, run:

    python3 scripts/cam-vps-release-inventory.py

The defaults are:
- backend root: /opt/creative-asset-manager
- frontend root: /var/www/creative-asset-manager

The script performs **no writes/deletes**, never reads application credentials,
and returns JSON showing each immutable release, an approximate size in MiB,
whether it is protected by current/previous or a live process, and warnings.
It fails closed if `current` is absent, symlinked outside the release root,
or otherwise invalid; only directories matching exact commit-style release
names are considered for review. Symbolic links and unknown entries are never
deletion candidates.

**Fail-closed process coverage (updated 18 Oct 2026):** the inventory reports
`PROCESS_SCAN=COMPLETE` or `INCOMPLETE`. If any live-process cwd/executable
cannot be read due to permissions or unexpected errors, the script reports
`process_references_incomplete` and downgrades every otherwise unprotected
release from `REVIEW_ONLY` to **`UNKNOWN`** (`unknown_count`).
If `du` cannot determine an individual release size, that release is likewise
`UNKNOWN` with `release_size_unavailable`. A missing `previous` symlink
sets `ROLLBACK_POINTERS=INCOMPLETE` and similarly excludes all unprotected
releases from `REVIEW_ONLY` until rollback anchors are verified.
Only ordinary disappearing processes (ENOENT/ESRCH) are ignored. Process cwd/exe coverage is not a
complete open-file or systemd reference audit, even if marked `COMPLETE`.

`REVIEW_ONLY` **does not mean safe to delete**. It means the helper did not
see an explicit protection in its bounded view. A human operator must also
verify systemd units, Nginx current config, deployment locks, pending rollouts,
backend/frontend rollback policy, disk headroom, and protected backup proof.
Missing `previous` is flagged for follow-up, not automatically created or
silently repaired.

The CodeLocal workspace is restricted to its checkout; do not invoke a script
through that checkout to bypass filesystem access control. Have a separately
authorized production operator run this inventory.

## Deletion conditions (not automated here)

1. P0 backup checksum and **real isolated restore proof** have been recorded
   privately; any planned backend migration/cleanup remains blocked otherwise.
2. Production `current`, `previous`, Nginx and every worker PID are verified.
3. The candidate release is not running, not the requested rollout candidate,
   not the current/previous rollback anchor, and not pinned by any process.
4. The operator reviews an explicit name/size manifest, chooses bounded
   retention and uses only the existing guarded release-management entrypoints.
   Never run generic `rm -rf` or `docker system prune --volumes`.
5. Check /live, /ready, version, queues, RAM and disk before and after.
   Stop if any health regression occurs.

## Synthetic tests

    python3 -m unittest scripts/test_cam_vps_release_inventory.py -v

Tests create disposable release directories under temporary storage and cover
current, previous, process references, broken symlinks, unexpected entries and
read-only behavior. They do not inspect, modify or purge production releases.

## Remaining P0 dependencies

The restore-helper mock tests and isolated synthetic GitHub CI PostgreSQL
rehearsal verify code behavior only. An actual managed production Drive backup
must still be independently identified, downloaded, checksum-verified and
restored to a separate PostgreSQL cluster with source identity and Alembic
validation. See `docs/operations/VPS_DATABASE_RESTORE_REHEARSAL.md`.
