# CAM Stage 1: Creative Brief V3 Fast Lanes V1

## Findings on 2026-11-02
- Nine Creative V3 native image outputs were previously generated **serially** in one
  Codex CLI lease. Production examples took about 20-30 minutes each.
- Six displayed Failed jobs completed within 4-6 seconds with the generic
  `codex_image_provider_error`, before producing any published image.
- A transient Drive upload failure for V3 nine-slot jobs was checked against
  six instead of nine checkpoint files, preventing a no-regeneration resume.

## New execution path
- New nine-slot generation and **manual retry** snapshots set
  `creative_v3_parallel_slots=2`. Already running attempts keep their pinned
  sequential mode. No schema change or bulk rerun.
- Two async lanes each use an independently acquired **cross-process file-locked
  Codex profile**. A profile cannot exceed its configured max_concurrent.
  If the optional second profile is busy, disconnected or lacks the Skill,
  the job continues on its existing single lease with no failed slot.
- Each slot remains one isolated native ImageGen call with a unique attempt
  workspace and exact SHA-256 native provenance. A previously verified PNG
  checkpoint is reused, not regenerated. Both lanes share one in-memory
  uniqueness check; the final files are assembled in strict slot order 1-9,
  preserving 1 Typography-first, 4 Creative Twist and 4 Explore directions.
- On any lane error, do not start further ImageGen calls. The other currently
  running slot is allowed to finish its independent verified checkpoint.
- Use low Codex *text reasoning effort* for the short tool dispatch while
  keeping the identical ImageGen quality and 2048x1024 RGBA QA gates.

## Fail-fast protection
- A native CLI session exiting in under 45 seconds with no verifiable cache
  result AND no JSONL imagegen tool event gets the code
  `creative_v3_codex_cli_early_exit`, not an unhelpful generic provider error.
- It may **defer once** for 60 seconds when no slot checkpoint exists. When
  there are valid independent slots, Stage 1 uses existing bounded recovery
  and resumes only the missing files.
- The specific failed account receives a cross-worker 5-minute Stage 1 V3
  cooldown. Other stages and healthy Codex profiles are unaffected.
- There is no paid API fallback or automatic wholesale regeneration.

## Cost and latency
- Nine image-generation calls are still required for nine original designs,
  plus at most the existing QA technical repair limit for invalid native PNGs.
- With two healthy independently leased Codex accounts, a batch may finish
  closer to half the old 20-30 minute runtime; this is an **estimate**, not a
  measured production SLA. One available account means original serial time
  may apply (with less CLI reasoning overhead).
- Per-slot verified completion duration and requested/used lane counts are
  recorded in worker telemetry and output-version metadata.
- Historical outputs, font plans, selections and Output History are untouched.

## Release/validation
- Test parallel completion and ordering, one-account fallback, checkpoint
  resume, native PNG provenance, cooldown, automatic zero-native recovery and
  9/9 Drive upload retry. Test Stage 2 regression since Codex account pool
  is shared with other product stages.
- Deploy from main, backend only, `cam-rebuild-backend.sh --no-migrate`.
- Run only one manually selected new/failed keyword as a pilot. Never force
  rerun hundreds of Stage 1 keywords when enabling this optimization.
