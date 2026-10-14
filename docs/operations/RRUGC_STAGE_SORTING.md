# RRUGC Stage 2–5 sorting

Stage 2 (both Products and Reviews colorway views) and Stage 3/4 source tables
use `GET /api/v1/realistic-review-ugc/source-plans` with `sort_by` and
`sort_dir`. They sort **all filtered source groups server-side before
pagination**, not just the current page. Queries reset pagination to page 1.

- Stage 2: source/filename, last updated, status.
- Stage 3: source/folder, last updated, analyzed, embroidery group size, status,
  Scout progress, Drive-ready reference count.
- Stage 4: source/folder, active queued/running job count, completed output
  count, Drive-ready references, group size, last updated, status.
- Both Stages 3/4 retain separate sort preferences when switching tabs.
  Metrics for an embroidery group sum across all its source-plan members.
- Scout progress mirrors the existing display logic: `drive_ready` for
  campaigns with auto-import and approved/importable references otherwise.
  Reference count is Drive-ready reference count; manually rejected/synthetic
  reference negatives do not count.

Stage 5 loads image history by cursor and has a scoped client-side sort:
newest/oldest, folder, image count, average scored UGC quality, or review
status (needs attention first). Image cards inside each loaded folder follow
the selected sort, and modal keyboard navigation follows the displayed order.
No claim is made that a client-only sort covers images not yet loaded; the
UI explicitly notes this limitation and exposes Load older images.

On the keyword Stage 0/1 tables, the active sort column gets a low-opacity
background tint across its header and visible cells. Stage 2 colorway tables
highlight the corresponding design, status, or last-updated column, with compact
Status and Last updated cells added to align the visible table with its sort
dropdown. ASC uses teal and DESC uses indigo; the tint is a translucent
background-image overlay so existing hover and selection states remain legible.
Non-sortable columns, skeleton placeholders, and empty messages stay unchanged.

All sorts are stable with deterministic filename/folder/ID ties; original
images, source assignments, job versions, and progress state are unchanged.
API data is not mutated by Stage 5 sorting.

Regressions: `test_stage_sorting.py`, `test_source_plans.py`,
`StageSortControls.test.tsx`, `RealisticReviewUgcPage.test.tsx`;
Browser QA plan includes sort controls on desktop, tablet, mobile.
