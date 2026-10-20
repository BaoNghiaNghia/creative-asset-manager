# Final-only asset persistence — Codex → CAM Managed Google Drive

Status: enforced for final-output selection and blocked for automatic
unclassified Codex image archives. Full per-job Codex tool-cache cleanup is
**not implemented yet**. Do not misrepresent "no lasting previews" as
"the Codex CLI never writes temporary PNGs".

## User-selected policy

- **Persist only completed FINAL images** in the tenant's configured Managed
  Google Drive product/output folder. A final may include multiple required
  deliverables, e.g. Stage 1's six `output/final/design_NN.png` images or
  the two independently finished V4 presentation boards.
- **Never upload** interim images that exist for internal review: draft
  concepts, low-quality previews, discarded variants, QA screenshots,
  iterations, mockup experiments or intermediary colorway renders. These
  are **not final deliverables**, even if the Codex tool writes PNGs in its
  own `CODEX_HOME/generated_images` cache.
- Reviews of *completed* Stage 3 UGC imagery remain a **downstream use
  of a final image** and are not forbidden. "Review image" here means a
  temporary visual created to inspect an in-progress generation job.
- Per-job local **temporary inputs and working previews** may exist while
  generation/validation runs. No durable media in Codex HOME: Codex auth,
  skills/config/session metadata stay. Never delete an in-flight image.
- A final with failed upload or unverifiable remote object is held as a
  retryable checkpoint; it must not be silently converted to Completed or
  deleted locally.

## Final selection contracts in code

| Workflow | Final allowlist | Forbidden |
|---|---|---|
| Stage 1 six designs | Exactly six numbered PNGs in `output/final/` | `working/previews`, review and QA |
| V4 independent concepts | Validated, independently assembled `*_01_design_concepts.png` and `*_02_13_colorways.png` boards in `output/vNNN/` | All intermediate `v4-job/artworks`, per-concept drafts, rendered variants |
| Other multi-output workflows | Explicit `output/final/` only | Everything else inside `output/` or `generated/` |
| Single-output and Stage 2/UGC | Validated `output/final.png` | Preview, screenshot and temporary substitutions |

An output contract cannot infer that all images are final merely because
they are under `output`, `artworks`, `rendered`, or `generated`.
Missing complete finals => incomplete/retry/failure, never upload review
images as a replacement. Job-specific tests assert this.

## Drive and deletion safety gates

1. Codex generates within isolated job workspace (temporary).
2. Validate final filename contract, image integrity, count, contents and
   stage-specific criteria. Mark the set final only at the stage's terminal
   quality gate.
3. Save **only** that final set through CAM Managed Drive, in the source
   product/shop's authorized output folder (not the unrelated recovery
   archive). Capture tenant, job, output name, SHA-256, byte size, remote ID,
   status. For multi-output jobs checkpoint each upload individually.
4. Remote-readback verification of tenant, SHA-256, size, Google Drive MD5
   and non-trashed state before local deletion. Never trust an HTTP success
   or file ID alone.
5. On success, delete the exact job-owned temporary inputs/previews/cache
   (recheck ownership, inode and active lease), retain only records,
   thumbnails if generated on request, and final Drive references.
   On failure, retain only retry-required final checkpoints; schedule retry.
6. Periodic maintenance verifies Drive-backed finals and removes cache
   leftovers only when evidence is unambiguous. Never age-only-delete
   unproven final output or treat cache leftovers as final.

## Current behavior and remaining engineering work

- Existing Stage 1 prompt explicitly disallows preview persistence and
  `cleanup_previews` deletes `working/previews` while retaining final
  checkpoints if generation/upload fails.
- The `all_generated_images` collector is now restricted to explicit
  finals, and V4 prompts no longer request archiving intermediates.
- **Critical safety change**: the recurring
  `cam-codex-drive-archive.py` cannot persist unclassified
  `CODEX_HOME/generated_images` PNGs by default. Its historical archive
  mode is explicit `--legacy-recovery` (operator-only), NOT in systemd.
  The Drive-backed final cleanup timer remains available.
- **Not yet complete**: Codex's own imagegen can still write to shared
  `CODEX_HOME/generated_images`. Determining which of those belong to a
  completed/retrying job requires a per-job manifest/isolated cache or
  positive ownership mapping. Until implemented, unknown PNGs cannot be
  safely deleted; they must not be uploaded as final either.
- Build a per-job temp-cache manifest (source sha256/inode/mtime, account ID,
  job+lease ID, final classification), and cleanup transactionally only
  after Drive verification. Isolate worker homes if Codex imagegen supports
  it without altering account authentication. Tests: concurrency across
  two accounts, partial six-final retry, failed Drive upload, timeout,
  process crash, file replacement and stage-specific mixed final/review
  outputs. Monitor and alert on stale tmp bytes, pending uploads and
  unclassified cache growth.

## Operational caveat

Changing this policy requires backend immutable-release deployment
because the installed systemd services invoke scripts in
`/opt/creative-asset-manager/current/scripts`. Code pushed to GitHub
alone does not change their running behavior. Keep rollback and verify
`/version`, `/ready` and systemd journal before claiming deployment.
