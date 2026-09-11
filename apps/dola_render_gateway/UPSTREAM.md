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

Pending later hardening:

- bind loopback only;
- fail-closed internal bearer auth;
- absolute persistent runtime paths;
- internal request idempotency;
- authenticated generated-content endpoint;
- safe account provisioning;
- isolated Patchright/Chromium runtime;
- Xvfb;
- systemd service;
- dedicated runtime user;
- dedicated venv; and
- CAM video_generation integration.

## License / terms

At the vendored commit, the upstream repository has no LICENSE file. Its README.md states only: "For educational and internal testing purposes."

This statement does not expressly grant commercial, production, or redistribution permission. Legal review and applicable service-terms review remain production gates before any CAM deployment or use.
