/** Approximate Valucap 8869 side hoop geometry for the brim-left reference photo.
 *  Scales with the complete side image, including inside the hover magnifier.
 *  Blue = hoop boundary, red = stitchable field, cyan = design placement.
 */
export function BlueprintSideGuides({ decorative = false }: { decorative?: boolean }) {
  return <svg
    className="rrugc-blueprint-side-guides"
    viewBox="0 0 700 430"
    preserveAspectRatio="none"
    aria-label={decorative ? undefined : "Side guidelines: hoop boundary, sew field and design placement"}
    aria-hidden={decorative ? "true" : undefined}
    role={decorative ? undefined : "img"}
    xmlns="http://www.w3.org/2000/svg"
  >
    <path className="rrugc-blueprint-side-hoop" d="M 264 157 L 490 115 Q 509 112 526 122 L 597 170 Q 615 183 621 206 L 608 293 Q 605 318 581 330 L 312 355 Q 296 355 284 343 L 235 283 Q 224 270 230 250 L 249 177 Q 251 163 264 157 Z" />
    <path className="rrugc-blueprint-side-sew-field" d="M 275 198 L 577 219 L 568 312 L 286 290 Z" />
    <path className="rrugc-blueprint-side-design-field" d="M 371 228 L 501 237 L 494 287 L 374 278 Z" />
  </svg>;
}
