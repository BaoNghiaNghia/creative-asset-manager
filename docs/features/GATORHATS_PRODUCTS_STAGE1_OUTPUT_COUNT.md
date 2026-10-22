# GatorHats Products — Stage 1 configurable output count

Stage 1 accepts a configurable number of independently validated final artworks.
The current default is **6**, for backwards compatibility, but code does not
assume six outputs in generation, checkpoint, upload, cleanup, font selection,
or job UI progress. Supported values: 1–24.

## Configuration

Set `RRUGC_STAGE1_EXPECTED_OUTPUT_COUNT` in CAM production configuration
(default 6). New processing jobs snapshot the value into
`processing_jobs.payload_json.expected_output_count`, so changes do not alter
a queued job's expected number of final files. Existing pre-upgrade jobs use
the legacy default of 6.

Every final is named `output/final/design_NN.png` (01 through requested N).
The image provider verifies valid, distinct PNG finals and never imports
temporary preview files as final artworks. Retry/resume checks and font plans
use the same count, and Drive cleanup only considers previous versions after
all expected outputs have been safely published.

## Operational safety

* This change does not remediate expired Codex authentication or Google
  Managed Storage credentials. Those must be checked separately.
* Never clear failed-job retry counters or trigger bulk regeneration just to
  test this setting. Use a permitted canary job after Codex and Drive health
  pass.
* Keep the original generated-file checkpoints until a complete version is
  saved and verified on managed storage.
* Changing the setting affects **new queued processing attempts**; already
  queued attempts retain their stored value.
