# GatorHats Products — Stage 2 Three-Skill Photo Pipeline

## Decision and separation of concerns

Each row represents ONE approved Stage 1 embroidery PNG (source-plan id + revision).
Preserve the existing 13-stock-colorway batch as-is. Add a separate **photo pipeline**
on that same row, with three strictly sequential steps:

| Step | Exact installed skill | Shots | Count |
|---|---|---|---|
| 1 | `gatorhats-8869-ecommerce-v2-4` | 01 Front High, 02 Front Right 3/4, 03 Direct Right Side, 04 Back High | 4 |
| 2 | `gatorhats-8869-multihat-v2-4` | 05, 06, 07 multi-hat reference scenes | 3 |
| 3 | `gatorhats-8869-ugc-v3` | 08, 09, 10 lifestyle scenes | 3 |

Total: 10 independently addressable PNGs PLUS the existing 13 colorway outputs per row.

## Source packages inspected (2026-10-14)

- `gatorhats-8869-ecommerce-v2-4.zip`: SHA-256 `c7253cf30151b0d93bff5aae2c453d865e5071fb111681a8b7f85ec4eb6dd3f9`.
- `gatorhats-8869-multihat-v2-4.zip`: SHA-256 `2fe07ce2fcf4e2b2be6307054f2f47606c3bd19baae7296933c752db7d2f4cb0`.
- `gatorhats-8869-ugc-v3.zip`: SHA-256 `a339aab2ef6eb55f886b0be1edd3dcfc86ad21b74777732919f866308937f976`.

The files were supplied to ChatGPT, not to the production CAM skill store.
Do NOT substitute a generated/summarized SKILL.md for the exact user-supplied ZIP,
silently discard bundled QA scripts, or mark a package ready without required assets.
The Manage Skills uploader is the supported authenticated route for transferring
these bundles to CAM production. The worker remains DISABLED until installation,
reference-input capture, output persistence and retry semantics pass tests.

## Recommended UI

In the existing Stage 2 table row, add the compact pipeline cell:
`01 Ecommerce · 4` → `02 Multi-hat · 3` → `03 UGC · 3`, plus **Details**.

Details opens a 3-column (1-column mobile) expansion:
- skill name, pinned version, bundle checksum and real input/reference readiness;
- every shot as a separate thumbnail with its own output version history;
- a status badge: Not run, Needs input, Queued, Running, Completed, Error;
- per-step attempt count, start/end, elapsed and last error with an actionable message;
- buttons: Run pipeline, Retry failed step, Logs, View all outputs, Regenerate step.
- when a step has partial outputs, display completed shot thumbnails, not an empty card.

No fabricated output thumbnails or success statuses. The currently deployed first
increment is read-only installation preflight and UI layout; execution buttons
are intentionally disabled pending the durable queue implementation.

## Durable execution contract to implement before activating RUN

Use an independent table keyed by
`(tenant_id, source_plan_id, source_revision, step_key)` with:
`status, skill_name, skill_version, skill_sha256, processing_job_id,
attempt_count, retry_count, error_code, started_at, completed_at`.
Separate immutable per-shot output records keyed by
`(step_id, shot_number, generation_attempt)`, including remote file ID,
content hash, dimensions, mime type, QA status, generation request/version and logs.
Never use or overwrite `rrugc_colorway_jobs` for these 10 images.

State machine:
1. preflight all inputs and bind immutable snapshots and skill bundle hashes;
2. enqueue Step 1 only and reserve a Codex pool profile slot; heartbeat and log
   queue, claim, tool start, per-shot upload, QA, publish and failure;
3. each completed shot is uploaded atomically and becomes a durable checkpoint;
4. Step 1 passes only when all four validated shots are persisted; THEN enqueue Step 2;
5. Step 2 passes only after three validated shots; THEN enqueue Step 3;
6. Step 3 passes only after three validated shots, marking the row complete.

Retries: retry ONLY the failed step, preserving prior successful outputs. Resume from
the missing shot index, with an explicit regeneration action for a completed step
that creates a new output version; never delete previously approved outputs
until replacements are verified and safely published. Reject or pause on source
revision/stock/skill digest change instead of silently mixing versions.
Bounded automatic retry for network/rate-limit failures; terminal hard-QA failures
require review or a new generation attempt. Use the existing processing-job
event/logs infrastructure with an independent execution log per step/attempt.
Use lease/heartbeat and Codex pool admission; do not start 3 steps concurrently.

## Reference & QA requirements

Ecommerce: user-supplied exact artwork, official cap stock, FOUR hard camera-angle
references, exactly ONE physically justified visible right-side `GatorHats`
on shot 01 and distinct shot 02/03/04. Multi-hat: real stock JPEG ZIP verified,
three scene references, per-hat Madeira thread matching and side-placement QA.
UGC: 3 scene/model references, five-panel no split-front-seam fail-closed visual
QA, independent wearer-name pool, right-side text positioning and scene identity.
No written prompt substitutes for actual visual inspection; automated size/count
checks do NOT mark factory-correct images as passed.

## Release gate

- Exact three ZIP bundles installed, content digest and references confirmed.
- Runtime unit/integration tests for sequential job scheduling, burst retry,
  partial checkpoints, workflow isolation, cancellation and per-shot logs.
- Browser QA desktop/tablet/mobile with authenticated production session.
- An actual end-to-end run on a test design with 4+3+3 assets saved and verified.
- Production deploy only when no in-flight job will be involuntarily interrupted
  or maintenance is explicitly coordinated.
## Workflow-specific Manage Skills (2026-10-22)

GatorHats Products and GatorHats Reviews now have separate Manage Skills
screens and independent tenant-scoped default selections. The underlying
ZIP/version registry remains shared and permissions remain unchanged.

Products Stage 2 exposes **exactly seven independent Skill assignment slots**:
1. Ecommerce — 4 existing photos; exact approved Ecommerce bundle
2. Multi-hat — 3 existing photos; exact approved Multi-hat bundle
3. UGC — 3 existing photos; exact approved UGC bundle
4. 13 Hat Colors — 13 existing colorway outputs; existing execution lane
5. Additional Skill A — configuration only, no worker/output mapping
6. Additional Skill B — configuration only, no worker/output mapping
7. Additional Skill C — configuration only, no worker/output mapping

Seven assignments do NOT imply seven runnable workers or extra generated photos.
The total remains 23 outputs per design. The original three photo pipeline
workers remain disabled until the documented execution gate passes; slots 5–7
must never be presented as runnable without an approved execution mapping.
Each Stage 2 Product slot rejects duplicate assignment of the same Skill.
Review defaults are independent: Review Stage 2 (colorways), Review Stage 4
(UGC images). Product Stage 1 (Six Designs) also has its own default.

The existing legacy defaults are read-only fallbacks for old installations.
New changes write only short workflow keys that fit the existing 16-character
database column; no DB schema migration and no rewrites of historical job
Skill versions or generated outputs are needed.
