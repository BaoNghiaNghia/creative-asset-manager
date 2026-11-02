# CAM Stage 2 Slot 1: Embroidery Reference → After Inpaint V2.3

Date: 2026-11-02 | GatorHats Products Stage 2 Slot 1 only.
No change to TikTokShop Scout & UGC, GatorHats Reviews or Stage 1 artworks.

## User-visible outputs

1. **Embroidery Reference**: a complete, verified native ImageGen macro photograph
   of the source typography sewn in fine threads on light twill. Exposed at
   /products-stage2/ecommerce/jobs/{id}/output?kind=embroidery_reference.
   This asset is saved before the cap edit and visible as soon as Pass A completes.
2. **After Inpaint · Front Cap**: original Valucap 8869 factory front photo,
   masked local inpaint using that same saved macro for material, Stage 1 artwork
   for text and color, and pinned placement for crown geometry. Exposed at
   the existing /products-stage2/ecommerce/jobs/{id}/output endpoint.

Both images appear on Slot 1 card and Details grid. The all-outputs gallery
and per-version history also list/pass through reference URLs. The reference
is a real independent output; not the flat pigment placement guide.

## Execution contract and cost

V2.3 uses the existing two verified native Codex ImageGen calls on the existing
account pool. Pass A's PNG and SHA are verified and uploaded independently to
managed asset storage; a separate transaction checkpoints front_preview_*
on the Ecommerce job BEFORE executing Pass B. No new DB columns or migrations.

- **Retry inpaint · keep reference** is permitted on failed or completed Slot 1.
  Reuse checkpointed Pass A bytes; invoke ONLY Pass B and its existing geometry,
  thread, hue, stock-preservation QA. Leave version history and prior saved
  image unchanged until a new image passes QA. Do not automatically retry A.
- **Full regenerate · both images** on completed Slot 1 pins a new job flag,
  invokes Pass A, verifies and saves the new material checkpoint, then invokes
  Pass B. Previous reference and completed version remain accessible until the
  new reference is safely saved. Version metadata records the macro remote ID.
- Automatic retry after a Pass B error reuses that same checkpoint. Historical
  already-queued 2.2 jobs keep their pinned SHA/output contract. Explicit
  Retry/Regenerate upgrades to V2.3.
- The new pass does not add a third imagegen call, do photo synthesis outside
  the edit mask, or bypass the thick-ridges/stock-drift QA. A rejected final
  remains rejected, but Pass A remains accessible.

## Safety checks and rollout

- Existing completed "THIS IS MY COWBOY HAT" production Slot 1 job has both
  front output and a pinned native PNG macro: the UI can display the macro
  without spending Codex tokens. Other older photo skills with a flat internal
  preview MUST NOT be shown as verified Embroidery Reference.
- The output endpoint checks tenant ownership before fetching exact managed
  assets; historical versions pull the stored per-version Pass A remote ID.
- Tested: failed Pass B after checkpoint, zero new Pass A calls on Retry,
  forced new Pass A on Full Regenerate, existing 2.2 worker parity,
  URLs in list/history and two Slot 1 output tiles.
- Deploy main -> new backend and frontend; verify health and production UI.
  Remove old runtime release directories only after a healthy new release,
  preserving active /opt/creative-asset-manager/current, database,
  source assets and Drive files. No bulk re-generation initiated by deployment.
