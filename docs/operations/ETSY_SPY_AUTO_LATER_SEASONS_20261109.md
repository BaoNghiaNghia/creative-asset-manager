# CAM Etsy Spy — Automatic Later for Thanksgiving / Halloween

Date: 2026-11-09. Tenant: `boostgame`. Backend-only operator-requested routing.

## Behavior
- Every **current or future qualified listing** assigned a Thanksgiving/Halloween **topic match** by the same vocabulary as CAM's daily topic badges, in **any current title or search keyword**, is shown in **Later** rather than Eligible. This includes `Turkey Day`, `Gobble Gobble`, `Friendsgiving`, `Spooky`, `Pumpkin`, `Trick or Treat`, `Witchy`, `Haunted`, `Ghost` and `Hocus Pocus`, not just the literal words `Thanksgiving` and `Halloween`.
- One Etsy Listing ID appears only once in the Later view, even if multiple keywords or sources match.
- These are **classification rules**, not keyword blacklist terms. The Windows Spy continues scouting; no changes to the source database, keyword tree, Chrome, TM checks or search rules.
- Routing is scoped to the operating tenant `boostgame`; other CAM tenants remain unaffected.
- A prior manual Picked row remains in PostgreSQL for reversible provenance, but automatic seasonal Later has **visible precedence** in the parent tabs, summary metrics, day badges and the Windows complete Picked feed. No historical Picks or extraction receipts are deleted, and no existing extraction job is cancelled by this release.
- Attempting to Pick a forced-Later seasonal listing returns a clear HTTP 409. Attempting to dismiss its Later status returns 409. Existing unrelated Picked/Later behavior is unchanged.
- Manual Later dates retain the original action timestamp. Automatic seasonal Later uses the latest active Etsy observation timestamp for its day badges.
- Later rows include `laterAutoKeyword` and a descriptive `laterReason`, without adding frontend UI features.

## Data/model
- No Alembic migration, database rewrite, source data deletion, extra worker, AI call or new dependency.
- `apps/api/app/modules/etsy_spy/auto_later_keywords.py` defines the explicit tenant mapping. This version is an operator-owned rule, not a new UI setting.
- CAM backend query filters use a distinct set of qualifying active listing IDs, so a second nonseasonal Search/Shop alias for the same Listing ID cannot remain Eligible. Matching uses the centralized `CATEGORY_GROUPS` vocabulary with compact SQL `regexp_match` word boundaries, avoiding oversized nested punctuation SQL in the daily badges query. PostgreSQL compiles `~`, SQLite test fixtures use `REGEXP`. Seasonal routing checks phrases independently of Christmas/other badge precedence.
- Source qualifications (own-source Bestseller/Popular now, views >5 OR carts >=20, apparel-only, exclude Custom and blacklist) still gate the visible Later table.

## Tests and release
```bash
cd apps/api && .venv/bin/python -m pytest tests/modules/etsy_spy/test_etsy_spy.py tests/modules/etsy_spy/test_migration.py -q
git diff --check
bash scripts/cam-rebuild-backend.sh --commit <reviewed-sha> --no-migrate --keep-releases 2
```
Backend production verification: source revision, /live and /ready health, normal CAM sync, fresh agent Picked-feed checksum and Later counts via authorized UI when available. Frontend rebuild is unnecessary. Regression: test each seasonal synonym, query both daily `Eligible` and `Later` topic groups for the same date, and verify Past+Future sync, Picked precedence and tenant isolation. In the 2026-11-09 report, the Eligible screenshot still showed Thanksgiving=8/Halloween=4 because V1 only matched literal season names; this corrective release fixes the classifier vocabulary mismatch without a one-time data migration.

## Rollback
Deploy the previous reviewed backend release via the official script. No database downgrade is needed because the rule is code-only and non-destructive. Manual Picked selections were retained, so they become visible again when the rule is removed. Backfill of already synced listings is immediate on reads; no one-time migration is required.
