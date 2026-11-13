# Frontend dependency security — 2026-11-13 (Phase 17)

## Current state

- Phase 15 baseline: 39 npm advisories (2 critical, 5 high, 32 moderate).
- Phase 16 secure toolchain: 28 moderate advisories, none high or critical.
- **Phase 17 TipTap 3.33.1 migration: `npm audit` reports 0 critical, 0 high, 0 moderate, 0 low.**
- Vite 6.4.6, Vitest 5.1.0, Node >=22.12, patched markdown-it/browserslist/nanoid/source-map-js remain pinned in package-lock.json.

## Editor migration scope and compatibility

- Only `app/public-review/AnnotationEditor.tsx` directly integrates TipTap. It is the Public Review annotation composer and edit/reply entry point; other CAM screens do not import the editor directly.
- Upgrade all five direct TipTap packages in lockstep to 3.33.1. `@tiptap/extension-link` is now bundled by StarterKit and must not be added a second time.
- Preserve the persisted JSON nodes (`doc`, `paragraph`, `text`, `taskList`, `taskItem`), marks (`bold`, `italic`, `strike`, `code`, `link`), and `checked` task attributes. Disable new StarterKit underline and trailing-node behavior to avoid unplanned changes in round-trip output.
- Keep links click-disabled in the composer and require HTTP(S) URLs with both the default validator and the application policy. The read-only `RichAnnotation` renderer also validates URLs before creating anchors.
- Preserve the comment/reply/create/edit flow and the draft if submission fails. No API schema, database migration, worker, Scout or ImageGen code change.
- Tests verify TipTap 2 JSON hydration and submission, safe/unsafe link handling, checked tasks, emoji insertion, failed-save draft retention, and Public Review notes integration.

## Continuous security gate

The frontend CI runs `npm audit --audit-level=moderate --json` through `verify-dependency-audit.mjs --minimum-severity=moderate`. A failed audit lookup, any moderate/high/critical advisory, or invalid report blocks merge. Low advisories remain visible in the report but are not currently a merge blocker. The gate has independent positive, negative and failed-audit test cases.

## Release gates

- Full frontend unit suite, TypeScript, Vite production bundle, and route-split budgets must succeed.
- Browser QA for Public Review and responsive Etsy Spy/other workspaces must pass.
- Merge only after full repository CI is green. Deploy frontend-only from clean `main`, retain current + one rollback, and verify a read-only authenticated production smoke test.
