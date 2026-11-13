# Frontend dependency remediation — 2026-11-13

## Scope and result

- Baseline `npm audit` on Phase 15: 39 findings (2 critical, 5 high, 32 moderate).
- After lockfile and compatible toolchain updates: 28 moderate, **0 high or critical**.
- Runtime `npm audit --omit=dev`: the previous high `markdown-it` issue is resolved; 28 TipTap-related moderate advisories remain.
- Remediated build/test toolchain with Vite 6.4.6, Vitest 5.1.0, plus patched transitive packages including `markdown-it` 14.3.3, `browserslist` 4.29.5, `nanoid` 3.3.20 and `source-map-js` 1.2.2.
- Vitest 5 requires Node 22.12 or newer. Production browser JS remains static; CI and host build environments already run Node 22.

## Continuous check

The frontend CI now parses the complete `npm audit` JSON and **fails on any high/critical finding or audit error**. Moderate advisories are summarized rather than silently suppressed. The check uses the npm vulnerability database on each run, so newly published advisories can correctly block a future release.

## Deferred editor migration

TipTap 2.x remains in use in document editors; 28 moderate reports transitively originate in its core. The suggested TipTap 3.x upgrade is a major API migration, not a safe lockfile-only patch. Audit editor parsing/sanitization, custom extensions, persistent content roundtrip, mentions/tasks, links, keyboard shortcuts and cross-workspace rendering before upgrading. Do not automatically run `npm audit fix --force`.

## Guardrails

- No API, worker, database, Scout, ImageGen or Etsy filtering contracts changed.
- Use a clean worktree, full frontend tests, production build/route-split and responsive browser QA before merging.
- Deploy from a reviewed `main` revision with current and one rollback release only.
