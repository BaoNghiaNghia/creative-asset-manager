/** Valucap 8869 RIGHT SIDE: clamp hoop, printable sew field and design bounds.
 *
 * The reference supplied for the cap clamp uses a native 488 × 610 SVG
 * coordinate space and a 9° clockwise placement transform. Our photo is a
 * right-facing (horizontally mirrored) 700 × 430 atlas tile, so project the
 * THREE shapes together through the same calibrated affine mapping instead
 * of estimating separate hand-drawn quadrilaterals.
 *
 * Projection order: reference rotate(9°) -> mirror/fit to 700 × 430.
 * Keeping the reference geometry unchanged preserves all three relative
 * margins. Neither the atlas tile nor the reference embroidery is mirrored.
 */
export const RIGHT_SIDE_GUIDE = {
  viewBox: "0 0 700 430",
  // translate(568, -18) * scale(-1.06, 0.845) * rotate(9°, source pivot)
  transform: "translate(568 -18) scale(-1.06 0.845) rotate(9 303.86012554478054 362.66312948411974)",
  hoop: "M 208.53503011589822 423.61823217225503 L 399.18522097366287 423.61823217225503 L 408.01161869855935 421.8529526272757 L 413.30745733349727 418.3223935373171 L 418.6032959684352 413.0265549023792 L 462.73528459291776 360.06816855300013 L 468.0311232278557 351.24177082810365 L 469.79640277283494 338.8848140132485 L 466.2658436828764 330.058416288352 L 459.20472550295915 319.46673901847623 L 404.4810596086008 277.10002993897297 L 397.41994142868356 273.56947084901435 L 386.82826415880777 271.80419130403504 L 220.89198693075335 271.80419130403504 L 210.30030966087753 273.56947084901435 L 203.2391914809603 277.10002993897297 L 148.51552558660194 319.46673901847623 L 141.45440740668474 330.058416288352 L 137.92384831672615 338.8848140132485 L 139.68912786170543 351.24177082810365 L 144.98496649664335 360.06816855300013 L 189.1169551211259 413.0265549023792 L 194.41279375606382 418.3223935373171 L 199.70863239100171 421.8529526272757 Z",
  sewField: { x: 183.91820686116188, y: 311.8407313841656, width: 239.88383736723736, height: 71.74096070795885 },
  designField: { x: 242.69318931124772, y: 341.744566876115, width: 122.33387246706563, height: 41.83712521600946 },
} as const;

export function BlueprintSideGuides({ decorative = false }: { decorative?: boolean }) {
  return <svg
    className="rrugc-blueprint-side-guides"
    viewBox={RIGHT_SIDE_GUIDE.viewBox}
    preserveAspectRatio="none"
    aria-label={decorative ? undefined : "Right-side cap-clamp hoop, safe sewing field, and embroidery design bounds"}
    aria-hidden={decorative ? "true" : undefined}
    role={decorative ? undefined : "img"}
    xmlns="http://www.w3.org/2000/svg"
  >
    <g transform={RIGHT_SIDE_GUIDE.transform}>
      <path className="rrugc-blueprint-side-hoop" d={RIGHT_SIDE_GUIDE.hoop}/>
      <rect className="rrugc-blueprint-side-sew-field" {...RIGHT_SIDE_GUIDE.sewField}/>
      <rect className="rrugc-blueprint-side-design-field" {...RIGHT_SIDE_GUIDE.designField}/>
    </g>
  </svg>;
}
