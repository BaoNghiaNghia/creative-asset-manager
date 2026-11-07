# CAM Etsy Spy — Keyword Tree Grid V2 (2026-11-07)

## Scope

Implement the approved desktop reference layout as a two-column Keyword Tree workspace:

- Parent Keywords management: **40%** of the available content width.
- Mindmap / Tree exploration: **60%** of the available content width.
- Four live counters: keyword records, grouped nodes, duplicates merged and root groups.
- Parent rows are vertical management cards; show source Scout record counts and one clear two-state priority segmented control.
- Existing Mindmap/Tree tabs, expand/collapse, zoom/pan, inspector and original Scout history remain available.
- Wide desktop uses a strict `2fr 3fr` CSS grid; at <=1140px the panels stack and at <=720px the mindmap inspector follows the canvas for mobile readability.

## Priority behavior

The existing backend and Windows Scout priority contract uses an integer.

- `Ưu tiên` = priority `1..100`, larger priorities first; default value 50.
- `Không ưu tiên` = priority `0`, last in normal scheduling order, **not removed or blacklisted**.
- Changing either mode saves immediately to the existing tenant-owned parent-keyword table. The small numeric priority field is editable while prioritized; a Save button appears only for changes.
- Switching off and on within the same page session restores the previous positive value; after a reload, enabling an unprioritized root defaults to 50.
- The API validates `0..100`, preserving all original source rows. Existing Windows clients receive numeric priority unchanged; no migration or new field is necessary.

## Mindmap readability

Initial state shows grouped root nodes without auto-expanding a potentially huge branch. The user can expand nodes and pan/zoom; on large expanded graphs, the viewport avoids forcing all nodes to microscopic size. The deduplication model, raw Scout records and canonical history remain unchanged.

## Validation and deployment

- Frontend Vitest for Etsy Spy, Keyword Tree, Keyword Mindmap (including UI mode persistence and grid structure).
- TypeScript typecheck and Vite production build; validate generated dist manifest and hashed assets.
- API pytest for parent-keyword write/read and Windows agent sync with priority 0 and 1..100, access rights and migrations.
- Deploy backend from the pinned `main` SHA with `--no-migrate` before frontend, since older backend rejects priority 0.
- Deploy frontend from same pinned `main` SHA, retaining current + previous rollback.
- Verify production `/version`, `/ready`, `/build-info.json` and new hashed CSS/JS.
