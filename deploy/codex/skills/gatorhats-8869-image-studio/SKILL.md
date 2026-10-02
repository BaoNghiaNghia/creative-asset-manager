---
name: gatorhats-8869-image-studio
version: 3.0.0
description: Codex local image workflow for Valucap 8869 full product-image sets: ecommerce views, multi-cap scenes, UGC/lifestyle, revisions, QA, continuation, and packaging. Use when the active job is clearly a GatorHats/Valucap 8869 Image Studio task. Do not use this workflow for a color-scaling-only request whose sole goal is to reproduce one existing design across several 8869 colorways; that belongs to the separate Scale Image 8869 workflow unless the user explicitly asks Image Studio to create a full set for each colorway.
---

# GatorHats 8869 Image Studio — V3

Create and revise imagery only for **Valucap Five-Panel Twill Cap 8869**.

The goal is high product fidelity with minimal instruction conflict. Current job inputs define **what the product is**. Stock assets define **cap construction/colorway**. Approved references define **photographic treatment only**.

## 1. Route the request before loading references

Use one mode:

- **NEW_SET** — new artwork and/or a new full 8869 image set.
- **CONTINUE** — continue the current unfinished set from pending outputs.
- **EDIT** — change only a requested property of an existing image.
- **REGENERATE** — remake only specified failed image(s).
- **QA** — inspect outputs only; do not generate unless the user asks to fix failures.
- **PACKAGE** — package existing final outputs only.
- **COLOR_SCALE_HANDOFF** — request is only to reproduce one design across multiple 8869 colorways. Do not mix Scale Image recolor rules into Image Studio.

Interpret short follow-ups from the current task/session. `làm tiếp` continues the active job. `fix ảnh 3` targets image 3 only. Do not restart or ask for information already established in the current job.

A clearly new artwork starts a new job unless the user explicitly says it is a variant of the current one.

## 2. Source authority — highest to lowest

When sources conflict, use this order:

1. Current explicit user instruction.
2. Current job state: current artwork, selected colorway, current side-text state, valid user-specified thread choices, and approved master.
3. Exact stock asset in `assets/stock/` for construction, geometry, and official colorway.
4. `references/THREAD_POLICY.md` + `references/mauchi.txt` for thread selection.
5. `references/PRODUCT_SPEC.md` for stable 8869 product rules.
6. `references/APPROVED_VISUAL_STANDARD.md` and approved images for photographic style.
7. General model knowledge.

Approved references are **style anchors only**. Never copy their wording, artwork, people, dog theme, room, props, or exact thread colors unless the current job independently calls for them.

## 3. Load only what is needed

Do not read every file before every action.

### NEW_SET
Read:
- `references/STOCK_MANIFEST.md`
- `references/PRODUCT_SPEC.md`
- `references/THREAD_POLICY.md`
- relevant sections of `references/APPROVED_VISUAL_STANDARD.md`

Inspect:
- current user artwork;
- only the selected stock colorway's `front.jpg`, `side.jpg`, `back.jpg`;
- only approved reference images relevant to the output type being generated.

### CONTINUE / REGENERATE / EDIT
Reuse the current job state and approved master. Inspect only:
- the target/current image;
- the minimum stock angle needed;
- the relevant style reference;
- thread policy only when color/thread is being changed or verified.

### QA
Compare only against the current artwork, selected stock asset, thread plan, master identity, and `references/QA_POLICY.md`.

### PACKAGE
Do not reload visual references. Package only actual final files.

## 4. Input gate and job state

A NEW_SET requires:
- one official colorway listed in `STOCK_MANIFEST.md`; and
- one current front-artwork image.

Side embroidery:
- user wording -> preserve exactly;
- `no side text` / equivalent -> omit completely;
- otherwise -> default exactly `Your Text`.

If colorway or artwork is genuinely missing/ambiguous, ask one concise combined question. If readable artwork text is uncertain, do not guess; request a clearer source or preserve only what is visibly certain.

If a colorway is absent from `STOCK_MANIFEST.md`, never call it official. Use it only when the user explicitly requests a **custom mockup**.

Maintain a lightweight current-job state in the active task/session:
- artwork authority;
- selected colorway;
- side-text state;
- front thread palette;
- side thread choice;
- master identity;
- completed / pending / failed image numbers.

Do not carry product details from an old job into a new artwork job.

## 5. Artwork fidelity lock

The current uploaded artwork is authoritative for the front design.

Preserve:
- exact readable wording and spelling;
- capitalization and line order;
- important icons/ornaments;
- typographic hierarchy;
- recognizable layout and relative scale;
- intended color relationships where practical.

Remove an accidental rectangular image background; do not turn it into a patch/badge/print. Do not translate, rewrite, mirror, add, or omit readable content.

Adapt the artwork to realistic direct machine embroidery. Simplify only micro-details that cannot plausibly stitch, while keeping recognizable identity. Front artwork colors follow `THREAD_POLICY.md`; **do not automatically recolor the front artwork to match the bill**.

Any readable text error is a **BLOCKER**.

## 6. Default 10-image structure

Unless the user specifies another structure, create **10 standalone square images**, never a collage/contact sheet/storyboard:

1. Straight-front ecommerce.
2. Front-right 3/4 ecommerce.
3. Direct-right-profile ecommerce.
4. Straight-rear ecommerce.
5–7. Three distinct multi-cap compositions.
8–10. Three meaningfully different UGC/lifestyle scenes relevant to the current artwork.

### Ecommerce 1–4
- full cap visible;
- consistent product scale/margins;
- visually clean neutral white field;
- no visible room, horizon, gradient, vignette, or colored cast;
- faint plausible contact shadow;
- neutral high-key light.

Do not treat generation as a pixel-level guarantee of RGB `255,255,255`. If mathematically exact white is required, that is a separate post-processing requirement.

### Multi-cap 5–7
- 3–5 physically separate 8869 caps;
- official colorways only unless user requests custom mockups;
- same current artwork identity and side-text policy across caps;
- natural spacing and controlled mixed angles;
- no merged hats, fused brims, or duplicated impossible geometry;
- vary setting/composition instead of repeating one scene three times.

### UGC 8–10
Use three distinct scene engines, such as:
- selfie / mirror / close worn portrait;
- contextual activity related to the artwork;
- two-person or small-group social proof.

Keep natural phone-photo realism, neutral daylight, believable skin/hands/cap fit, and enough visibility to identify the product. Theme comes from the **current artwork**, never from old dog/barber references by default.

## 7. Master-first consistency

For NEW_SET:

1. Inspect exact selected stock front/side/back and current artwork.
2. Build the thread plan using `THREAD_POLICY.md`.
3. Generate Image 1 as the identity master.
4. QA Image 1 for product construction, exact artwork, embroidery realism, colorway, placement, and forbidden branding.
5. If the user requested review-first mode, stop after the master.
6. Otherwise, once the master passes, use it as identity authority for Images 2–10.
7. Use exact stock geometry for ecommerce angle changes.
8. Change only scene/composition variables for multi-cap and UGC; preserve product identity.
9. Do not restart successful outputs because another image fails.

## 8. Delta-edit lock

For EDIT, freeze every property the user did not ask to change.

Example: `side text nhỏ hơn 20%` means change only side-text scale/placement as needed. Preserve cap model, colorway, front artwork, front thread palette, camera angle, lighting, crop, scene, and other accepted details as closely as possible.

Do not opportunistically "improve" unrelated areas during a targeted edit.

The specific target image must be available in the active workspace/task inputs. Never claim to edit an unseen or unavailable image.

## 9. Product non-negotiables

- Valucap 8869 five-panel silhouette with one broad uninterrupted front panel.
- No center seam through the front artwork area.
- Exact stock crown/bill/button/eyelets/rear opening/snap proportions and colors.
- Curved stitched bill; no flat-brim or trucker-mesh conversion.
- Realistic twill and direct embroidery; reject print/patch/rubber/plastic-looking treatment unless explicitly requested.
- Side personalization, when enabled, stays small on the lower right-side panel and changes visibility naturally with angle.
- Never add crocodile mark, GatorHats wordmark, THS, OTTO, retail sticker, invented label, or invented branding.

## 10. QA and retry budget

Use `references/QA_POLICY.md`.

Distinguish **BLOCKER** from **ACCEPTABLE VARIATION**. Do not regenerate endlessly for tiny harmless differences.

For a failed generated image:
1. Prefer one targeted correction when the failure is local.
2. If still blocked, allow one full regeneration of that image.
3. After **2 recovery attempts maximum per image**, keep the best valid candidate or report the remaining limitation instead of entering a loop.

Never silently lower standards for spelling, product construction, official colorway, forbidden branding, severe deformation, or missing personalization.

## 11. Packaging and truthfulness

Package only when requested. Include only final standalone files that actually exist and passed QA.

Use stable filenames:
- `01-front`
- `02-front-right`
- `03-right-profile`
- `04-rear`
- `05-multicap-1` … `07-multicap-3`
- `08-ugc-1` … `10-ugc-3`

Never claim a completed set, edit, or ZIP exists unless it was actually produced in the current session.


## 12. Codex runtime execution

This installed skill is invoked explicitly as `$gatorhats-8869-image-studio`.

- Use the available `$imagegen` capability only when the user/task actually requests generation or editing.
- Treat files in this skill directory as read-only product/reference assets.
- Keep generated job outputs in the active task workspace, never inside the installed skill directory.
- For a full set, generate and QA outputs sequentially so the approved master can anchor later images.
- Do not use an API-key-backed image-generation fallback when the runtime is configured for ChatGPT/Codex-authenticated generation.
- The separate `worker-hat-v1` RRUGC skill remains authoritative for `rrugc_generate`; do not claim or intercept that workflow.
