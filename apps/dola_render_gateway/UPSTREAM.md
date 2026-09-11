# Dola Render Gateway Upstream Provenance

Upstream repository:
https://github.com/coll3879xx-cyber/dola-render-gateway

Vendored commit:
ce1ebc0c4630d2afe0a03ca62490b4a3c92ea2d4

Vendored date:
2026-09-11

## Integration strategy

- Vendored snapshot, not a git submodule.
- CAM owns future local patches.
- Upstream updates require explicit re-audit.
- Do not blindly overwrite CAM patches.
- Every future upstream update must record the previous SHA, new SHA, date, diff summary, and compatibility/security review.

## Current status

**SOURCE ONLY - NOT PRODUCTION READY**

DG-01 adds a CAM-owned source wrapper with loopback-by-default binding, fail-closed internal bearer auth, absolute state/runtime paths, and argv-safe account provisioning. It remains source-only.

Still required before any production use:

- internal request idempotency;
- authenticated generated-content API contract;
- isolated Patchright/Chromium runtime;
- Xvfb;
- systemd service;
- dedicated runtime user;
- dedicated venv;
- CAM video_generation integration; and
- legal and service-terms review.

## License / terms

At the vendored commit, the upstream repository has no LICENSE file. Its README.md states only: "For educational and internal testing purposes."

This statement does not expressly grant commercial, production, or redistribution permission. Legal review and applicable service-terms review remain production gates before any CAM deployment or use.

## CAM local patches

All patches below apply on top of vendored upstream SHA ce1ebc0c4630d2afe0a03ca62490b4a3c92ea2d4; the recorded provenance SHA is unchanged.

- upstream/video_worker.py: removed one accidental duplicate triple-quote in generate_video. This file is runtime-reachable through server.py -> browser_pool.py -> video_worker_ui.py -> video_worker.py; without this minimal syntax fix the upstream server cannot import.
- upstream/config.py, server.py, and browser.py: accept wrapper-provided absolute SQLite, profile, download, extension, and static-web paths. The supported runtime no longer depends on CWD or writes persistent state under the source tree.
- upstream/media.py, video_worker_ui.py, and add_account.py: route temporary reference media and debug artifacts through wrapper-provided runtime directories.
- upstream/add_account.py: rejects secrets on argv; only an account identifier can be supplied there, while secrets are read by protected interactive input.

The raw upstream video_worker.py syntax defect was therefore runtime-required, not legacy/unreachable.
