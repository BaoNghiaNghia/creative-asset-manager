/** Calibrated right-facing placement shapes for the Valucap 8869 side preview.
 * Paths follow the reference cap image's 700×430 viewBox. The side cap photo
 * is mirrored independently; these paths must NOT be mirrored.
 * Blue: cap-clamp hoop, red: sew field, cyan: reference design safe area.
 */
export const RIGHT_SIDE_GUIDE = {
  hoop: "M 46 312 Q 45 292 57 276 L 113 223 Q 121 215 138 211 L 333 165 Q 351 159 368 170 L 451 224 Q 481 242 488 271 L 497 324 Q 501 344 479 357 L 164 402 Q 142 404 127 390 L 68 358 Q 46 346 46 312 Z",
  sewField: "M 57 267 L 391 210 L 406 337 L 72 373 Z",
  designField: "M 171 283 L 309 265 L 322 328 L 184 348 Z",
} as const;

export function BlueprintSideGuides({ decorative = false }: { decorative?: boolean }) {
  return <svg
    className="rrugc-blueprint-side-guides"
    viewBox="0 0 700 430"
    preserveAspectRatio="none"
    aria-label={decorative ? undefined : "Right-side guidelines: cap clamp hoop, sew field and Roberts design safe area"}
    aria-hidden={decorative ? "true" : undefined}
    role={decorative ? undefined : "img"}
    xmlns="http://www.w3.org/2000/svg"
  >
    <path className="rrugc-blueprint-side-hoop" d={RIGHT_SIDE_GUIDE.hoop}/>
    <path className="rrugc-blueprint-side-sew-field" d={RIGHT_SIDE_GUIDE.sewField}/>
    <path className="rrugc-blueprint-side-design-field" d={RIGHT_SIDE_GUIDE.designField}/>
  </svg>;
}
