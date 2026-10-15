# Branding DNA — CAM

**Current state:** Phase 1 Foundation implementation: tenant-scoped real Shop Registry (PostgreSQL + API), guarded create/list endpoints, status and worker registry read API. Browser scanning, agent enrollment, image evidence, Gemini processing and profile approval remain intentionally disabled. The design prototype is not a production crawler.

## What is committed

- [UI/UX and API blueprint](./BRANDING_DNA_UI_SPEC.md): screens, contract candidates, data model, status taxonomy, role/access model, responsive breakpoints and QA gates.
- [Original standalone prototype](./prototype/index.html): editable reference HTML/CSS/JS from the approved UI design.
- [Operational readiness audit](./READINESS_AUDIT.md): tested dependencies, gaps, installation/connection checklist and phased activation.
- [Dedicated worker architecture](./WORKER_ARCHITECTURE.md): recommended Windows scanner agent, alternatives, security protocol and rollout gates.
- [Pre-implementation optimization](./PRE_IMPLEMENTATION_OPTIMIZATION.md): P0/P1/P2 scope, canonical product identity, evidence quality and readiness fixes.
- [Reference architecture](./REFERENCE_ARCHITECTURE.md): relation to `creative-ai-hub`'s five Profile layers.
- CAM frontend preview page: `apps/client/app/branding-dna/BrandingDnaPage.tsx`
- CSP-compatible static prototype: `apps/client/public/branding-dna-preview/{index.html,prototype.css,prototype.js}`
- Preview tests: `apps/client/scripts/branding-dna-design-qa.mjs`

## Access

- In CAM `/branding-dna`, the **Shop Registry is real** and stores tenant-scoped draft shops; the lower iframe is an **isolated mock UI preview**. Reading requires `ai_operations.read`; creating a shop currently requires `ai_provider.configure` pending dedicated branding permissions.
- The former `/branding-character` route is retained as a **compatibility redirect** to `/branding-dna` (also for nested tab paths). New links and documentation use the new name.
- Branding Character is the deprecated design label; the product and all user-facing navigation are named **Branding DNA**.
- Browser frame has `sandbox="allow-scripts allow-downloads"` **without same-origin or form submission privileges**.
- Only the Shop Registry Add Shop action above the iframe writes real shop records. All actions **inside the iframe** remain examples. Scanning, listing/product evidence, profile aggregation, approval and worker enrollment are disabled.
- Mobile and desktop navigation show this destination only to authorized users.
- Do not claim shop intelligence works in production until data APIs, browser workers, platform eligibility, Gemini jobs, QA and opt-in activation are complete.

## Development

```bash
cd apps/client
npx tsc -b
npx vitest run app/AppRoute.test.ts app/components/WorkspaceNavigation.permissions.test.tsx app/branding-dna/BrandingDnaPage.test.tsx
node scripts/branding-dna-design-qa.mjs
npm run build
```

## Rollout

1. UI and first production Foundation: shop registry, guarded API routes and migration; no browser scans enabled.
2. Complete Foundation: dedicated Branding DNA permissions, worker registration/enrollment and audit logs, pagination and store-identity verification.
3. Controlled single-shop browser pilot using an **isolated Windows browser worker**, verified storefront URL and explicit user approval.
4. Ingest product image evidence and aggregate versioned profiles using existing Gemini capacity, with checkpoint/retry and review gates.
5. Connect approved profiles to skills/workflows (no automatic cross-shop style mixing).

## Phase 1 production foundation (2026-10-15)

- Migration `0157_branding_dna_foundation` adds `bc_shops` and `bc_browser_agents`.
- Tenant-scoped API endpoints: `/api/v1/branding-dna/status`, `/shops`, `/workers` and disabled `POST /shops/{id}/scan-runs`.
- Shop creation is initially admin-scoped via existing `ai_provider.configure`, until dedicated Branding DNA roles are introduced.
- Scanner is **not enrolled** and bulk crawling remains disabled; the registry has real persistence, but product evidence/Brand Profiles are still future work.
