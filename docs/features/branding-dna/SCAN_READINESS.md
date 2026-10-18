# BD-SCAN-READINESS-01 — Scanner readiness dashboard

Implementation: 2026-10-18. **Not a marketplace crawler or task-dispatch release.**

## What operators can inspect

In CAM → Branding DNA → Browser Scanner, the **Điều kiện quét theo shop** panel
selects a registered Amazon/Etsy shop from the current tenant. It requests
`GET /api/v1/branding-dna/shops/{shop_id}/scan-readiness` and displays:

- Tenant-scoped snapshot totals, including the number marked `verified`.
- Durable metadata task counts by staged/pending/leased/retry/blocked/completed/cancelled.
- Number of paired Scout Manager agents seen and online (fresh heartbeat
  within the existing 3-minute presence TTL, agent still active).
- Explicit unmet requirements for platform authorization, per-shop access,
  scanner dispatch and marketplace-specific permissions.

The endpoint requires `ai_operations.read`, returns `Cache-Control: no-store`,
and answers `404` for shops outside the active tenant. It reads existing
`bc_shops`, `bc_listing_snapshots`, `bc_scan_tasks`, paired agent presence and
RRUGC agent activity. No new schema, secrets, marketplace requests or migrations.

## Non-negotiable safety boundaries

- `scan_enabled=false`, `dispatchable=false` and `active_scan_jobs=0`.
- An agent heartbeat or user-declared `owned/authorized` shop **does not**
  independently verify Amazon/Etsy's rights, platform policy or image rights.
- Staged tasks remain staged; this endpoint does not call `claim_next_task`,
  change queue state or supply arbitrary URLs to Windows workers.
- The marketplace rights blocker is always present until a separate audited
  rights/eligibility feature is developed. Etsy requires written authorization;
  Amazon requires Agent Policy review and an identifying User-Agent.
- Source `verified` counters mirror existing stored state, **not an
  independent verification performed by this dashboard**.
- Agent count is global to the tenant's paired session pool, not evidence that
  any single agent was granted permission for the selected shop.

## Verification

```bash
cd apps/api
.venv/bin/python -m pytest tests/modules/branding_dna/test_scan_readiness.py tests/modules/branding_dna/test_scan_tasks.py tests/modules/branding_dna/test_scout_presence.py -q
cd ../client
npm run typecheck
npm test -- --run app/branding-dna/BrandingDnaPage.test.tsx
```

The current source branch must pass repository release and production backup
gates before deployment. This stage improves operational transparency only;
a separate versioned, authorized dispatch/result protocol is still necessary.
