# CAM Etsy Spy: conservative occasion-only Later routing

Date: 2026-11-11
Scope: CAM API Etsy Spy classification and derived related-Later matcher only.
Source of truth: origin/main at ad462831c before this patch.

## Root cause

The old automatically Later rule reused broad CATEGORY_GROUPS phrases and
matched the joined listing TITLE OR source SEARCH KEYWORD. A generic pumpkin,
ghost, witchy or spooky product was therefore treated as Halloween, and a
Christmas design seen under a Thanksgiving keyword could be put into Later.
The SQL predicate looked at ANY Search/Shop alias of a listing ID, forcing
the whole product into Later even if a different title explicitly identified
a competing occasion. Later-related manual seed matching could also
propagate a shared generic ghost/witch motif across disjoint occasions.

## New behavior

- The auto-Later policy still belongs only to the configured tenants
  boostgame and creative-assets-prod, and still targets Thanksgiving and
  Halloween. Unconfigured tenants have no automatic Later.
- Only explicit, high-confidence occasion PHRASES from the listing TITLE
  can route automatically. Thanksgiving: "thanksgiving", "friendsgiving",
  "turkey day", "gobble gobble". Halloween: "halloween", "trick or treat",
  "all hallows eve". Broad ghost/pumpkin/witch/spooky/haunted motifs, movie
  catchphrases and source search keywords are not sufficient evidence.
- A title with another explicit occasion (Christmas, Easter, Valentine's,
  Mother's/Father's Day, New Year, birthday, wedding, anniversary etc.)
  cannot auto-Later, even if also SEO-tagged Thanksgiving/Halloween.
- All current Search/Shop TITLE aliases for one Etsy listing ID are examined.
  A generic alias plus an unambiguous occasion title is safe; conflicting
  explicit title occasions anywhere under that ID veto auto-Later for it.
  The SQL predicate and Python API label helper share the SAME vocabulary.
- Manually chosen Later seeds remain durable. Related Later matching does
  not cross explicit occasions, or pull in an occasion-unknown item from
  an explicitly seasonal seed solely on design motif. A generic ghost/
  witch alone does not create a related Later relationship.
- Previously Picked rows, source observations, manually selected Later,
  dismissed Later matches, blacklist, SMS and extension Queue V3 are not
  deleted or rewritten. Group facet badges remain independent of Later
  routing. The new automatic classification is a read-only view.

## Current data correction

Auto-Later is derived on each query: all historically false auto-Later
rows reappear in Eligible/Picked immediately after the API release,
provided they are qualified and neither manually Later nor matched by a
separate still-valid manual Later rule.

A separate bounded maintenance script,
scripts/reconcile_etsy_later_occasion_scope.py, audits the persisted
derived related-Later matches. Dry-run is the default. Apply requires:
a fresh 24-character fingerprint and a verified backup no more than six
hours old. It updates only derived matches for reviewed Listing IDs
through refresh_later_matches; it keeps manual Later seeds/dismissals,
picked status and source records intact. Run it only through the approved
production maintenance route after verifying a database backup and
reviewing the dry-run. Do not silently mutate the source inventory.

## Tests and deployment

Run the full apps/api/tests/modules/etsy_spy suite on an isolated
staging copy with the production Python dependencies; 69 tests pass,
including exact title matching, aggregate alias contradictions, cross-
occasion seed exclusions, day-group views, picking, sync, migrations and
tenant isolation. No new libraries and no database migration.

Deployment: merge reviewed code into main, use the official
scripts/cam-rebuild-backend.sh --commit SHA release workflow, verify
/live and /ready and authenticated Etsy Spy API smoke; retain the
current and previous immutable releases. Rollback uses the official
backend --rollback entrypoint, not a database restore, unless a
separately reviewed derived-match reconciliation was applied.

Remaining limitation: title-only explicit matching prioritizes low false
positives over exhaustive holiday classification. A pumpkin-only design
may remain Eligible even when a human would call it Halloween. This is
intentional: manual Later remains available for such ambiguous designs.
