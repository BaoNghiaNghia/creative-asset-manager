# QA Policy — Blockers vs Acceptable Variation

Use this file to prevent both under-QA and endless regeneration.

## BLOCKER — must fix

A result is not final if any of these occur:

### Product identity
- wrong cap model/construction;
- six-panel center seam through front art;
- mesh/flat-brim/incorrect silhouette;
- selected official colorway materially wrong;
- rear opening/snap/button/geometry materially inconsistent with stock.

### Artwork / personalization
- readable spelling, capitalization, line order, important icon, or recognizable layout changed;
- artwork mirrored or replaced;
- front artwork unintentionally recolored to bill color;
- side text wrong, oversized, misplaced, duplicated, or present when disabled;
- forbidden branding/labels/stickers invented.

### Embroidery
- reads as print, rubber, plastic, pasted patch, or implausible foam effect when direct embroidery is intended;
- severe stitch distortion destroys legibility/identity.

### Output-specific
- wrong required ecommerce angle;
- ecommerce background visibly colored/gradient/room/horizon;
- cap cropped when full product is required;
- multi-cap hats merge/deform;
- severe anatomy/hand/cap-fit failure in UGC;
- unrelated dog/barber/person/theme leaks from approved references.

## ACCEPTABLE VARIATION — do not regenerate only for this

- tiny stitch-direction differences that preserve readable identity;
- minor natural contact-shadow density variation;
- white background visually clean but not mathematically every pixel `#FFFFFF`;
- subtle perspective compression expected from 3/4/profile views;
- natural UGC asymmetry, fabric wrinkles, skin texture, or phone-camera exposure variation;
- tiny non-semantic thread microtexture differences.

## Recovery budget

For one failed image:
1. local failure -> targeted edit first;
2. if unresolved -> one full regeneration;
3. maximum 2 recovery attempts per image before reporting the remaining issue or choosing the best valid candidate.

Never waive blockers involving text, product construction, official colorway, major deformation, forbidden branding, or personalization state.
