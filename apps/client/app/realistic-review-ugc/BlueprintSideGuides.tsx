/** Calibrated right-facing placement shapes for the Valucap 8869 side preview.
 * Paths follow the reference cap image's 700×430 viewBox. The side cap photo
 * is mirrored independently; these paths must NOT be mirrored.
 * Blue: cap-clamp hoop, red: sew field, cyan: reference design safe area.
 */
export const RIGHT_SIDE_GUIDE = {
  hoop: "M 2 345 Q 8 305 39 266 L 92 208 L 297 174 Q 325 169 347 184 L 439 237 Q 461 252 468 284 L 473 331 Q 473 351 446 363 L 149 419 Q 124 426 104 409 L 35 376 Q 7 368 2 345 Z",
  sewField: "M 57 267 L 391 210 L 406 337 L 72 373 Z",
  designField: "M 145 295 L 301 271 L 318 335 L 155 352 Z",
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
