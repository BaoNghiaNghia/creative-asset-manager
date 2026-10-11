# RRUGC GatorHats sidebar submenu — 2026-10-11

The GatorHats Products and GatorHats Reviews selector is now part of the **global workspace sidebar**, nested under the collapsible Realistic Review UGC menu. The previous in-page two-group header has been removed: within a group the page displays only the three relevant Stage tabs.

- **GatorHats Products:** Stage 0, 1, 2. URL `/realistic-review-ugc?workflow=products&stage=stage0`.
- **GatorHats Reviews:** Stage 3, 4, 5. URL `/realistic-review-ugc?workflow=reviews&stage=stage3`.

The parent link defaults to Products; the expand button opens/closes the two subitems without navigating. Parent and selected child are marked, while children follow the existing `realistic_review_ugc.read` permission gate. Existing `?stage=stageN` deep links still work. A mismatched group/stage in the query loads the group's default, preventing cross-group mismatches. Stage selection updates the URL without adding a history entry or dropping other URL parameters.

On tablets (681–1024 px), where the desktop sidebar is hidden, the two workflow links are shown immediately below the workspace navigation. On phones (≤680 px), tapping the UGC tab opens an accessible choice sheet for Products or Reviews, with keyboard Escape/backdrop close and focus restoration.

All six Stage panels retain their existing IDs, data loading, Skill settings, job controls and original API interactions. This is a frontend-only change; it does not migrate or delete generated assets, database records, job history or intermediate images.

Verification: targeted unit tests for sidebar permissions, URLs, UI switching, mobile choice sheet, TypeScript checks, 4-viewport fixture-backed UI QA, all-frontend Vitest suite, build provenance and public production smoke. The QA plan uses **same-origin** scenario navigation, never a cross-site redirect. Fixture and public-route QA are not substitutes for authenticated QA with real user data. Any old visual baselines showing the previous in-page selector are intentionally obsolete and must be reviewed, not silently overwritten.

Production deploy: `bash scripts/deploy-cam-frontend.sh --commit HEAD` on a clean reviewed checkout (CodeLocal Live Project with standing user approval); if CodeLocal host policy blocks the operation, the trusted operator can use the same reviewed command via SSH. Validate live `/build-info.json`, `/ready` and read-only smoke. No backend deploy is needed.
