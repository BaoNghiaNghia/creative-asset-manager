# Windows pilot readiness — 2026-11-25 ICT

Workstation: DESKTOP-3FR0LF0
Pilot directory: D:\POD Automation\CAM Muse Hybrid Gateway
Base hybrid core commit: 7954df4aed4b2e702329a548f53a05f92392b4db
Source files copied and read back; all five matched after newline normalization.

## Installed and verified

- Python 3.13.17, Python.Python.3.13, WinGet user scope.
- FFmpeg and FFprobe 9.1-full_build-www.gyan.dev, Gyan.FFmpeg,
  WinGet user scope.
- WinGet independently reported verified installer hashes.
- Windows test invocation: py -3.13 -m unittest discover -s . -v
  from the pilot directory.
- Result: 32 tests, 1.832 seconds, OK, no skipped tests.
- Linux baseline: 32 tests, OK.

The optional check_runtime.ps1 was blocked by Windows script execution policy.
Do not override that policy. Run normal Python and executable commands directly.

## Compatibility not established

Pinned LynByte source 6050d45d3c99d89664cfff0a0a5c830aa9569f36:
src/lib/museDriver.ts explicitly navigates https://muse.ai/ and
https://muse.ai/thread/new. Existing Muse-01 CAM automation is documented as
using media.generate_video. No evidence yet establishes that the public
muse.ai service is the same renderer/account/runtime. Do not install or
start the unmodified upstream gateway or change authentication based on
the shared name alone.

Required before live use: identify the actual authorized renderer and
documented integration interface; verify runtime/API compatibility and
permission; integrate the real existing CAM lease, Original SHA, hero,
QA, cooldown, task-resume and receipt callbacks; harden any actual
gateway task/idempotency/retention server; then arrange exclusive cutover
and three genuine 9:16 canary jobs. Mock tests and H.264 fixtures do not
certify renderer or visual product QA.

## Production verification

2026-11-25 13:40:38 ICT: CAM Bridge 1.4.23, health OK, API active,
NRestarts=0, vertical_first, continuousGenerationEnabled=true,
storyLiteEnabled=true. Release 089bd59a6611703703a2b1fe394f6343ca2f813f.
No production claim, render, account change, scheduler change, database
migration, or new backend release was performed in this setup.
