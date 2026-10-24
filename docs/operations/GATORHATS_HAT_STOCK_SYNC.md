# GatorHats 8869: Sync Hat Images by color and view

## Purpose

The **Sync Hat Images** action appears in Products > Stage 2. It previews an
uploaded ZIP, groups factory photos by the 13 supported official color keys,
and applies only complete Front/Side/Back sets. Optional 3/4 shots are stored.
It does not edit the embroidery artwork, existing output versions, stage jobs,
or any PostgreSQL row.

## Naming

Filename **or directory** can provide the color and view. Case, spaces,
underscores, dashes, and 3/4 notation are normalized. Examples:

- `Natural Black Front.jpg`
- `8869/Natural Black/Side.jpg`
- `Natural-Black/Back.jpg`
- `Front/Natural_Navy.png`
- `Natural Navy 3.4.jpg`

Colors accepted: Khaki/Maroon and Natural/{Black, Brown, Camo Green, Charcoal,
Forest Green, Khaki, Maroon, Mossy Oak Breakup, Navy, Realtree All Purpose,
Red, Royal}. Unknown colors, ambiguous views, duplicate views, and incomplete
sets are reported; only complete sets can be applied. An angle name is
required explicitly, never inferred from image order.

## Workflow

1. Navigate to GatorHats Products > Stage 2; select **Sync Hat Images**.
2. Choose a ZIP (<=120 MiB), then **Preview groups**.
3. Inspect 13-color group matches, missing angles, duplicates and ignored files.
4. Confirm **Sync ready color groups** to import complete sets only.
5. Refresh or revisit the page. The 2x2 Embroidery Design thumbnails and
   Ecommerce input previews now use the same versioned Natural/Black stock;
   Stage 2's 13-color *future* runs can use imported color Front images.

The uploaded ZIP is uploaded twice (preview then apply); the backend verifies
its SHA-256 both times. The previous importer versions remain on disk and can
be reactivated through a separately reviewed manifest rollback.

## Persistent data / rollback

Uploaded stock lives under the configured persistent
`CODEX_IMAGE_HOME/gatorhats-hat-stock`, independent of release directories.
Images are immutable under `sets/{color}/{sha256}/`. A manifest is published
with an atomic replacement protected by file locking. API/worker reads use a
shared lock for the full Ecommerce stock set, preventing mixed revision angles.

To roll back a color's imported stock, revert its `manifest.json` entry to a
verified previous full revision (or remove only that color's manifest entry to
use the packaged fallback). **Do not remove any stored image versions during
rollback.** Reverse backend/frontend application changes using standard CAM
release rollback scripts only. Never run/requeue prior AI jobs for stock sync.

## Security / operations

- Read/catalog/photos require `realistic_review_ugc.read`; preview/apply
  require `realistic_review_ugc.configure`.
- Image ZIP: 120 MiB compressed, 240 MiB uncompressed, 400 files,
  16 MiB/image and 36 megapixels/image. Unsafe paths and symlinks are ignored;
  MIME is checked against actual decoded image type.
- No database migration, provider key, new package or secret is required.
- Deployment: build from `main`, deploy backend and frontend using reviewed
  production scripts only after all QA gates clear; check API health, routed
  catalog and GatorHats Stage 2. Browser baseline acceptance is separately
  governed; do not approve unrelated screenshot differences automatically.
- Production reverse-proxy request-body size for the 83 MB customer ZIP has not
  yet been independently confirmed; uploads may return HTTP 413 until the
  approved proxy configuration is verified by the production operator.
- Empty uploads, no complete color groups or disk failures fail closed.
- Prior completed outputs are preserved; only later job input stock may change.

## Audit only

`python scripts/hat_photo_grouping.py /path/to/8869-images.zip`

This prints filename-based grouping without modifying stock or the database.
