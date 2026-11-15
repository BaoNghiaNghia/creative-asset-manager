import type { DesignAsset } from "./api";

const CURRENT = "colormatch-sms-c1717-v9";

function knownPosition(value: string): string | null {
  const position = (value ?? "").trim().toLowerCase();
  if (!position || position === "unknown" || position === "other") return null;
  return position;
}

export type ProductColorAdvice = {
  positions: string[];
  colors: string[];
  awaitingAnalysis: boolean;
  printReviewRequired: boolean;
  // This is a color recommendation intersection, NOT validation of a
  // garment SKU, print method, inventory or final production sample.
};

/** One variant per real print position. The currently viewed variant is
 * authoritative for its position; never mix alternate variants together.
 */
export function sharedGarmentColors(
  designs: DesignAsset[], activeIndex: number,
): ProductColorAdvice | null {
  const picked = new Map<string, DesignAsset>();
  for (const design of designs) {
    const key = knownPosition(design.position);
    if (key && !picked.has(key)) picked.set(key, design);
  }
  const active = designs[activeIndex];
  const activePos = active && knownPosition(active.position);
  if (active && activePos) picked.set(activePos, active);
  if (picked.size < 2) return null;
  const activeDesigns = Array.from(picked.values());
  const positions = Array.from(picked.keys());
  if (activeDesigns.some(design => {
    const match = design.color_match;
    return !match || match.version !== CURRENT ||
      match.output_sha256 !== design.output_sha256 ||
      !Array.isArray(match.eligible_colors);
  })) {
    return { positions, colors: [], awaitingAnalysis: true, printReviewRequired: true };
  }
  const matches = activeDesigns.map(design => design.color_match!);
  const safeSets = matches.map(match =>
    new Set((match.eligible_colors ?? []).filter(c =>
      !(match.unsafe_colors ?? []).includes(c),
    )),
  );
  const shared = Array.from(safeSets[0]).filter(color =>
    safeSets.every(set => set.has(color)),
  );
  shared.sort((a, b) => {
    // Balanced rank across *all* print positions. Maximize worst-position
    // acceptability by considering the lowest rank first.
    const ranksA = matches.map(m => (m.eligible_colors ?? []).indexOf(a));
    const ranksB = matches.map(m => (m.eligible_colors ?? []).indexOf(b));
    return Math.max(...ranksA) - Math.max(...ranksB) ||
      ranksA.reduce((s, n) => s + n, 0) - ranksB.reduce((s, n) => s + n, 0) ||
      a.localeCompare(b);
  });
  return {
    positions,
    colors: shared.slice(0, 10),
    awaitingAnalysis: false,
    printReviewRequired: matches.some(m => m.analysis?.print_review_required === true),
  };
}
