# Branding DNA — Company-owned Amazon/Etsy onboarding

Scope confirmed by the operator on 2026-10-21: the configured Amazon and Etsy shops
belong to the company; the catalog will contain multiple Amazon storefronts.
These facts describe the intended catalog, **not** independent proof of
marketplace automation permission.

## Shop identity and isolation

The existing `bc_shops` table supports multiple Amazon and Etsy shops per
tenant. Each record has a unique `(tenant_id, platform, region,
canonical_shop_key)`; a different Amazon storefront must have its own shop ID,
browser checkpoint, reports and future scan tasks. `Amazon` is a platform,
**not** a singleton job slot. The current canonical key includes the normalized
storefront path; multiple page URLs for the same storefront can therefore
need operator de-duplication until a verified stable seller/store identifier
adapter is implemented.

## Bulk company ownership declaration

`POST /api/v1/branding-dna/shops/owned-declarations/bulk`
requires the existing `ai_provider.configure` permission and an explicit list
of **up to 200** shop IDs with their genuine internal proof references:

```json
{
  "acknowledge_marketplace_authorization_not_verified": true,
  "items": [
    {
      "shop_id": "11111111-1111-4111-8111-111111111111",
      "evidence_reference": "company-owned-store-registration-A"
    },
    {
      "shop_id": "22222222-2222-4222-8222-222222222222",
      "evidence_reference": "company-owned-store-registration-B"
    }
  ]
}
```

The example UUIDs and references are *illustrative only*. Do not submit them
as real evidence. Never fabricate references or automatically mark all future
shops as company-owned without a specific attestation.

Validation is tenant-scoped and all-or-nothing: duplicate or foreign IDs,
missing shops, unsupported platforms, malformed references, oversized batches
and missing acknowledgement are rejected before any commit. Matching records
are replayed idempotently, and changed documentary references create a new
append-only declaration requiring a separate reviewer.

The API does not change `shop.status`, `shop.access_mode`, agent permissions,
scan tasks or marketplace authorization. Each shop still needs a distinct
second-person review before catalog activation. Etsy automation requires the
necessary platform-specific written authorization; Amazon requires an agent
policy/access review. Browser scanning stays disabled until audited lease and
permitted adapter implementation is deployed. Per-shop local pilots remain
separately permission-gated, bounded and manually initiated.

## Rollout order

1. Operator supplies genuine shop identity and proof references for each
   Amazon/Etsy storefront and submits the bulk declaration.
2. An independent curator reviews each shop and activates permitted catalog
   entries using the existing controlled lifecycle endpoint.
3. Verify platform-specific automation permissions and permitted browser
   adapter behavior (no CAPTCHA/rate-limit/login bypass).
4. Only then deploy/audit a per-shop tenant-scoped scan lease protocol,
   heartbeat, checkpoint, retry and quarantined evidence handoff.
5. Verify on an authorized Windows host against a bounded sample; leave all
   unapproved shops in standby.

No schema migration, database backfill, existing Chrome-profile change or
automatic scan is performed by this source change.