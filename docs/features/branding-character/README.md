# Branding Character — CAM

**Current state:** Stage 0 UI prototype integrated into the CAM frontend source. No live shop scan, storage, Gemini analysis or Brand DNA approval is enabled. This repository content is not a production crawler.

## What is committed

- [UI/UX and API blueprint](./BRANDING_CHARACTER_UI_SPEC.md): screens, contract candidates, data model, status taxonomy, role/access model, responsive breakpoints and QA gates.
- [Original standalone prototype](./prototype/index.html): editable reference HTML/CSS/JS from the approved UI design.
- [Operational readiness audit](./READINESS_AUDIT.md): tested dependencies, gaps, installation/connection checklist and phased activation.
- [Reference architecture](./REFERENCE_ARCHITECTURE.md): relation to `creative-ai-hub`'s five Profile layers.
- CAM frontend preview page: `apps/client/app/branding-character/BrandingCharacterPage.tsx`
- CSP-compatible static prototype: `apps/client/public/branding-character/{index.html,prototype.css,prototype.js}`
- Preview tests: `apps/client/scripts/branding-character-design-qa.mjs`

## Access

- On a local CAM build, `/branding-character` and tabs under it display an **isolated mock preview** behind the existing `ai_operations.read` permission.
- Browser frame has `sandbox="allow-scripts allow-downloads"` **without same-origin or form submission privileges**.
- Actions are examples only. Shop names, products, scan progress and Profile approvals are not connected to API data.
- Mobile and desktop navigation show this destination only to authorized users.
- Do not claim shop intelligence works in production until data APIs, browser workers, platform eligibility, Gemini jobs, QA and opt-in activation are complete.

## Development

```bash
cd apps/client
npx tsc -b
npx vitest run app/AppRoute.test.ts app/components/WorkspaceNavigation.permissions.test.tsx app/branding-character/BrandingCharacterPage.test.tsx
node scripts/branding-character-design-qa.mjs
npm run build
```

## Rollout

1. UI + documentation merged on `main` without enabling browser scans.
2. Foundation: migrations, tenant-scoped entities and API RBAC plus audit logs.
3. Controlled single-shop browser pilot using an **isolated Windows browser worker**, verified storefront URL and explicit user approval.
4. Ingest product image evidence and aggregate versioned profiles using existing Gemini capacity, with checkpoint/retry and review gates.
5. Connect approved profiles to skills/workflows (no automatic cross-shop style mixing).
