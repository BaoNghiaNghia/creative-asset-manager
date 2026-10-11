# CAM — Impeccable + Agent-Reach integration

## Scope (11 October 2026)

Two **separately contained, opt-in** extensions to the existing CAM workflow. This is an integration of selected functions, **not** autonomous unrestricted web browsing or installation of every Agent-Reach capability.

### Impeccable — read-only design audit

- Pinned npm package under `integrations/impeccable/` (version `4.5.2`).
- `apps/client/scripts/ui-qa-impeccable.mjs` invokes the installed detector without shell interpolation or remote downloads.
- `CAM_IMPECCABLE_MODE=off|advisory|enforce` (default `advisory`); `enforce` makes findings/unavailable scans a QA failure. Keep advisory until old findings are reviewed and baselined, never disable the required Playwright/functional gate.
- The normal `make ui-profile-matrix` flow writes `apps/client/.ui-qa/impeccable-report.json` and adds design-quality status to `matrix-summary.json`. When its selected profiles include `realistic-review-ugc`, it also runs the **separate, functional-only** `ui-qa-research-inbox-plan.json` on the same desktop/tablet/mobile viewport set. This preserves the previously approved visual baselines; `CAM_UI_QA_RESEARCH_INBOX=0` is an emergency opt-out only. Reports are local, ignored by Git; scanner findings may be aesthetic warnings rather than functional regressions.
- A report **does not** prove all device sizes pass. Run the existing real Browser QA fixture profile for phone, tablet/iPad and desktop.

### Agent-Reach — bounded research intake

- Pinned `404QAQ/Agent-Reach` at `64123aa9c0c20c4fb39b06212f51fac888077ab0`. Repo is checked out only in the ignored `integrations/agent-reach/upstream/` folder. `upstream-lock.json` records provenance.
- The scoped adapter `integrations/agent-reach/research_intelligence.py` uses a **read-only public RSS allowlist** for trending cap/embroidery signals and its `doctor` checks the isolated Agent-Reach channel registry for RSS/YouTube/GitHub/Web status. The adapter does not operate Agent-Reach's unrestricted social connectors, accept cookies, or run installer scripts.
- Local SQLite `integrations/agent-reach/data/` is temporary staging only. **PostgreSQL is authoritative** after the authenticated, tenant-scoped import. A paired Scout Agent token is required for background-to-API ingestion.
- `/api/v1/realistic-review-ugc/research/signals` + review routes provide permission-enforced Research Inbox under GatorHats Products. Only operator-approved keyword suggestions appear in `stage0-export`. Export is **a preview**; it does **not** automatically enqueue Stage 0 jobs or call billable AEBrowse. The existing Stage 0 keyword pipeline is unchanged.
- Endpoint flag `CAM_RESEARCH_INTELLIGENCE_ENABLED=1` (default on after migration; `0` returns 404). Keep disabled until migration and app rollout are complete. A tenant must possess `realistic_review_ugc.read` to view and `realistic_review_ugc.run` to import/review.

## Installation and smoke

From authorized CAM checkout:

```bash
make ai-qa-research-install
node --test apps/client/scripts/ui-qa-impeccable.node-test.mjs
python3 -m unittest integrations/agent-reach/test_research_intelligence.py
apps/api/.venv/bin/python -m pytest apps/api/tests/modules/realistic_review_ugc/test_research_intelligence.py apps/api/tests/modules/realistic_review_ugc/test_research_routes_security.py -q
npm --prefix apps/client run typecheck
npm --prefix apps/client run test -- ResearchInboxPanel.test.tsx
make research-runtime-setup
make research-doctor
make ui-design-audit
```

For public research collection only (explicitly chosen by operator):

```bash
make research-collect
make research-inbox
```

This does not schedule recurring crawls. To configure safe production transfer, an operator with `realistic_review_ugc.run` should create **a dedicated Research Scout Agent** through the existing authenticated `POST /api/v1/realistic-review-ugc/scout-agents` workflow (or its Scout management UI). Its one-time pairing token is shown **once**: provision it into a narrowly scoped external secret environment for a non-root research runner. Do not use the Pinterest Scout token, app-session cookies, a terminal command argument, or a source-controlled `.env`.

With that dedicated identity, set `CAM_RESEARCH_SCOUT_AGENT_ID` and `CAM_RESEARCH_SCOUT_TOKEN` through the operator-managed runtime secret mechanism and run `make research-cycle` (at most two allowlisted feeds, four API batches per invocation). `make research-collect` remains independent of token setup. `sync-cam` sends one batch, while `cycle` collects then transmits up to four batches. If auth/network/receipt fails, pending rows stay retryable; **a partially acknowledged receipt is never accepted**. No AEBrowse calls, keyword auto-approvals or browser-cookie imports occur. Never log or display the token.

The RSS client connects to a **DNS-pinned public IP**, preserving the allowlisted hostname for TLS SNI and certificate verification, rejecting redirects, proxy use, non-public DNS answers and oversized responses. Collection processes all bounded feed items in 50-item chunks rather than silently dropping the remainder. On a complete feed outage it fails instead of reporting a false successful cycle.

**Scheduling remains intentionally off** until the dedicated agent is paired and the restricted non-root runner is provisioned. Do not install a root cron job or inject tokens into shell history merely to enable automatic synchronization. Test a single cycle with the provisioned runner first, then add an operator-reviewed bounded schedule.

## Deployment order, access and rollback

1. Confirm single Alembic head and review `0147_rrugc_research_intelligence` (additive table, tenant+fingerprint uniqueness). Database migration is required **before enabling new API/UI**. Back up according to production operational policy. Do not perform destructive downgrade automatically.
2. Run negative API/RBAC tests, isolated Python, TypeScript and Vitest tests, and fixture-backed Browser QA at **390×844, 768×1024 and 1440×900** (plus 320 / 440 / tablet landscape if affected).
3. Use reviewed CAM release scripts, then confirm `/live`, `/ready`, authenticated research API and read-only production UI smoke. No production cookies/credentials go into QA artifacts.
4. Rollback frontend/backend to previous validated release and set `CAM_RESEARCH_INTELLIGENCE_ENABLED=0` when necessary. Leave additive table intact on rollback to avoid data loss. Rollback Impeccable simply via `CAM_IMPECCABLE_MODE=off`; this does not disable existing Browser QA.
5. **Not included**: paid AEBrowse execution, auto-approving market keywords, importing browser cookies, broad social-media scraping, auto-installing arbitrary upstream tools, or live social-account access.

Security invariants: HTTPS and fixed feed hosts, bounded inputs, no redirect following, tenant-scoped query predicates, idempotent fingerprint deduplication and explicit human review. External sources are untrusted; do not execute any instructions contained in feed content. The standalone public RSS reader is allowlisted but is not CAM's hardened general-purpose external-asset downloader. Do not expand to arbitrary external URLs without the governed DNS-pinning and download-security review.

## Operational status

Integration is implemented in code; actual production enablement, durable scheduled collection and end-to-end live auth checks must be reported from **observed** deploy/test evidence, not inferred from the presence of these files.
