# CAM Branding DNA — Shop Registry without ownership-approval gate

Decision: 2026-10-21.

## Registry behavior

- A shop is a tenant-scoped source record, **not** a platform access grant.
- Save, view, open in the default browser, and set lifecycle status without
  creating a CAM `owned` or `authorized` declaration or second-party review.
- Shop `access_mode` and legacy declarations/reviews are retained only as
  historical/descriptive metadata for compatibility. They are never used
  to decide whether a source may be opened or whether a local browser pilot
  can proceed.
- A deliberately `paused` source is withheld from local pilots. A `draft`
  source is a saved registry record, not a failed ownership check.
- The existing 33 shop URLs, browser profiles and evidence records are
  preserved; no migration drops or overwrites existing data.
- Authentication, per-tenant authorization for editing CAM's registry and
  validated HTTPS storefront URL restrictions remain intact.

## External access and automation are separate

- Ordinary user-initiated **Open shop** opens a storefront in the system
  browser and does not create a job, load product data or modify shop state.
- The user-initiated browser pilot requires valid per-shop external access
  approval and currently applicable marketplace rules; its existing checks
  are unchanged. Etsy written permission / Amazon Agent Policy are independent
  of any CAM classification.
- CAPTCHA/access-denied/login challenges stop automated access. No proxy
  rotation, stealth, lock bypass or hidden marketplace API is provided.
- CAM production dispatch remains disabled pending separate authorization,
  audited lease handling and explicit release.
- Historical ownership declarations/reviews remain queryable to avoid data
  loss, but are no longer part of activation, readiness or browser eligibility.

## Verification

- Worker regression suite includes an authorized local pilot for a
  `draft`/competitor-labeled shop and a paused-shop refusal.
- API tests verify tenants cannot change other tenants' shops, lifecycle
  changes create no tasks and do not confer external rights, and the
  dispatch-preview remains non-executable.
- Client UI has no ownership approval or second-party review form.