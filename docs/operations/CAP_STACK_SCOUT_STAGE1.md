# Cap Stack Scout — GatorHats Reviews Stage 1

This lane is a **shared tenant-wide** Pinterest photo library, not a Review
Stage 3 campaign, source plan, product or embroidery group.

## Where to control it

- CAM → GatorHats Reviews → **Stage 1 / Cap Stack Scout**: enable/disable
  the server queue; filter shared images, review with ✓ / × / AI, and manually
  record 1–50 visible caps per image.
- **RRUGC Scout Manager for Windows, version 0.1.19**: separate Cap Stack
  Scout card with Start/Pause/Restart, PID, health, and independent live logs.
  The existing Review and Keyword lanes remain independently controllable.
- `START_SCOUT_CAP_STACK.cmd` is an alternative direct process launcher;
  do not use it simultaneously with the third Manager card.

Both switches must allow work: the Stage 1 server queue switch **and**
the Windows Scout Manager global Run automation plus Cap Stack local mode.
Queue starts disabled on first deployment and waits without launching
Cap Stack Chrome until enabled. This allows safe rollout without surprising
the production Windows workstation.

## Isolation / discovery

- The Cap Stack mode runs the existing Python Scout v55, but uses a **third**
  persistent Chrome profile `pinterest-profile-cap-stack` (or
  `RRUGC_CAP_STACK_PROFILE_DIR` in the ignored `scout.local.env`).
  This path must be distinct from Review and Keyword profiles.
- Initial Pinterest keyword exactly `Cap Stack`; subsequent run searches
  include short stacked baseball/trucker-cap queries, ranked by Cap Stack-only
  human feedback. A Review-specific human/lifestyle query suffix is never
  appended. Duplicates are dropped both by local history and tenant-scoped
  unique Pinterest numeric Pin ID.
- Images submitted to the authenticated API are **never linked to product,
  embroidery groups, Stage 3 campaigns or Review candidate feedback**.
- The server owns an exclusive 45-minute lease per tenant. Every task caps
  submissions at 12 new images; successful tasks cool down 15 minutes.
  Failures/login gates cool down for 30 minutes. Manual Pause invalidates the
  server lease. Review/Keyword Scout are left untouched.

## Feedback and learning

- ✓ marks a Cap Stack reference as appropriate, × as inappropriate, AI as
  likely AI-generated, with Undo to clear; updates are last-write-wins per
  image. These labels are scoped to this shared library only.
- The next Cap Stack query ranking favors keywords with a better approval
  yield. This **query ranking is live** and does not require a model retrain.
- **Gemini pool vision (revision 0155)**: each newly ingested Cap Stack image
  is queued through `rrugc_cap_stack_analyze`, using the SAME Gemini
  credential selection, quota coordinator, per-model start gates and shared
  RRUGC concurrency reservation as candidate/source/UGC analysis. No new key,
  external vision provider, or separate quota is used.
- A bounded maintenance backfill (max 6 active queued jobs per tenant,
  priority 4, no starvation boost) processes old pending images too. Existing
  Stage 3 candidate and source jobs take precedence. Quota/cooldown errors
  defer without consuming retries; hard failures are recorded with a safe
  error code and a manual **Retry Gemini analysis** action.
- Vision returns `is_cap_stack`, count of *visually distinct* target caps
  (0–50), confidence (0–1), uncertain AI suspicion (NOT definitive AI
  provenance), and a short pixel-grounded summary. A count under 66%
  confidence is flagged `needs_review` and non-stacked imagery is flagged
  `not_cap_stack`. This is an AI estimate, **not a verified ground truth**.
- Human corrections are stored separately as `manual_caps_count`,
  prioritized over the AI suggestion and never overwritten by regeneration.
  Likewise ✓ / × / AI manual labels are independent from Gemini suggestions.
- Automated image similarity grouping/visual deduplication is still not
  implemented. Pinterest numeric Pin IDs are deduplicated exactly.
- The source picture is an original Pinterest image URL, not a rehosted
  R2 image CDN item; future ingestion should cache compliant thumbnail
  renditions privately with authorization and size quotas.

## Order of deployment

1. Verify and apply migration `0154_rrugc_cap_stack_scout`.
2. Deploy API/worker code. Validate that the new Cap Stack queue is
   **disabled** and the original Review/Keyword APIs remain responsive.
3. Deploy the Stage 1 frontend and run a fixture-backed Browser QA pass
   on desktop, tablet, mobile. Do not confuse fixtures with live PIN data.
4. Build and distribute the **Windows** Tauri 0.1.19 x64 installer/portable
   executable through the Windows CI workflow. Git source auto-update can
   refresh `scout.py` and the PowerShell launchers, but cannot update
   the native binary on a workstation by itself.
5. Operator manually updates the Windows Manager; preserve pairing token,
   existing Review/Keyword browser profiles and history. Turn on Stage 1
   and use the new mode. Verify new Pinterest Pins appear in the library.
6. Pause Stage 1 to stop new scans or stop the Cap Stack child process
   independently; no Review or Keyword data requires rollback.

## Verification boundaries

Backend tests cover tenant isolation, PIN/URL validation, duplicate
ingestion, lease expiry, toggle/feedback and human cap count.
Windows launcher tests cover distinct profile/runner/Manager flags.
The authoritative Windows Rust build and installer test runs on GitHub's
Windows runner; the VPS cannot run `cargo` without a Rust toolchain.
