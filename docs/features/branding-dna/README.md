# Branding DNA — CAM

**Current state (source review 2026-10-16):** Tenant-scoped real Shop Registry (PostgreSQL + API) and dedicated worker **enrollment / revocation / heartbeat / empty lease control plane** are present. Browser scanning, listing/image evidence collection, Gemini DNA analysis, profile approval, SMS/Scout generation and supervised learning are **not yet operational**. The prototype is not a production crawler. The consolidated design below is documentation, not a release.

## New: Shop Knowledge RAG
- [BD-RAG-01 — Shop Knowledge implementation and remaining gates](./SHOP_RAG_IMPLEMENTATION.md).
- [BD-SCAN-READINESS-01 — Scanner readiness, agent connectivity, task and rights blockers](./SCAN_READINESS.md).
- [BD-SCAN-CALLBACKS-01 — Fenced worker checkpoint/failure HTTP contract (dispatch remains OFF)](./SCAN_CALLBACK_CONTRACT.md).
- [BD-SCAN-CALLBACKS-02A — Durable terminal failure receipts and isolated worker SQLite outbox (dispatch OFF)](./SCAN_FAILURE_RECEIPTS.md).
- Shop-scoped document upload, text extraction, review/approval and citation-based lexical retrieval have an isolated API and Shop Knowledge tab.
- Brand DNA **Input Readiness** now displays per-shop listing verification blockers, approved Shop RAG citation excerpts and a reproducible input fingerprint. It is a read-only evidence packet, not AI DNA synthesis or auto-approval. This is a *separate source of contextual evidence*, not a replacement for product/gallery images, Best Seller signals, or the approved DNA Core.
- Upload: TXT/MD/CSV/JSON/DOCX/text PDF; 2 MiB/file; strict document and lifetime limits per shop. No OCR, embeddings, generative responses or automatic profile mutation.
- Uploaded RAG context remains untrusted until reviewed; even approved chunks never override verified product attributes or official platform rules.

## What is committed

- **[Consolidated product/system specification (2026-10-16)](./PRODUCT_CREATIVE_SYSTEM_SPEC.md)**: Brand DNA = All Products + Product/Image Pick + Best Seller, distinct DNA Core/Creative Playbook, 3 Team Inputs, **SMS-only A** and **Scout-only B**, 6 output groups and governed Output Supervised Learning.
- **[Development plan & acceptance gates](./PRODUCT_CREATIVE_IMPLEMENTATION_PLAN.md)**: phase work packages, data/API interfaces, roles, dependency contracts, security, QA, source integrations and rollback.

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

- In CAM `/branding-dna`, the **Shop Registry and worker control-plane UI are real** (tenant-scoped draft shops, enrollment/heartbeat/disable). Scanner, Product Explorer and Brand DNA intelligence remain non-operational. Reading currently uses `ai_operations.read`; configuring shops/workers uses `ai_provider.configure` pending dedicated branding permissions.
- The former `/branding-character` route is retained as a **compatibility redirect** to `/branding-dna` (also for nested tab paths). New links and documentation use the new name.
- Branding Character is the deprecated design label; the product and all user-facing navigation are named **Branding DNA**.
- The static HTML prototype remains isolated at `/branding-dna-preview/`; no user action there triggers production scans or AI processing.
- Shop Add and Worker Enroll/Disable are real authenticated actions. The separate prototype contains examples only; scanning, listing/product evidence, profile aggregation and approvals remain disabled.
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
- Dedicated worker enrollment, revocable one-time Bearer tokens, 60-second heartbeat and a deliberately empty lease endpoint are implemented in Phase 2. Agent is not a crawler.
- Worker package: `apps/branding-dna-worker/` (Node.js LTS, heartbeat only; **not installed on a Windows host automatically**).
- Migration `0158_branding_dna_agent_auth` adds per-agent token hashes, enabled/revocation state, and agent version.
- API: admin `POST /api/v1/branding-dna/workers`, `POST /workers/{id}/disable`; agent `POST /workers/{id}/heartbeat`, `POST /workers/{id}/lease` (no jobs until adapters are ready).
- Scan remains **disabled** until platform authorization, durable job leasing, evidence, and Gemini aggregation are implemented and tested.

## Phase 2 worker control plane (heartbeat-only)

- Migration `0158_branding_dna_agent_auth`: enabled, token hash and version metadata on `bc_browser_agents`.
- Tenant-admin enrollment and disable operations, one-time token, Bearer heartbeat and a no-op lease endpoint that **always returns no scan job**.
- UI shows online/offline/disabled worker states. Dedicated Windows program in `apps/branding-dna-worker/` communicates via HTTPS without Playwright/shop crawling.
- The old iframe preview is a standalone link at `/branding-dna-preview/` to respect CSP `frame-ancestors none` globally. The operational `/branding-dna` SPA URL never collides with a static directory.
- A Windows host must be explicitly connected and provisioned before it can heartbeat. Zero agents is an expected Phase 2 initial state.
