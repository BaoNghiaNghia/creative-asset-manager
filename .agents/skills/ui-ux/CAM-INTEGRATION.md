# evondevKit for CAM Codex CLI

Pinned upstream: `evondev/evondevKit` commit `5a892ceac93d1bb286391663eb01cbd011706886` on `master`.

Version pin and the full upstream `skills/ui-ux` folder were copied into this repository for repeatable, reviewable Codex CLI use. License: MIT; see `LICENSE`.

From the CAM project checkout use `$ui-ux` when working on interface code. Let the skill inspect CAM's existing components and design tokens, and request direct-build mode for bounded work when an already-approved design direction is available.

The skill is NOT a runtime library, should not change product/listing prompts, and must not be automatically injected into production image generation or TikTok listing skills. Update manually after review, with visual regression tests. The pinned skill's own update-check script may check upstream versions but does not install updates.
