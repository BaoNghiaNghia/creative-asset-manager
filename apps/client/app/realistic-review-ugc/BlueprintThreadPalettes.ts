/** Dark thread combinations inspired by the supplied Valucap 8869 brim colors.
 * Swatches are preview approximations, NOT physical thread/SKU specifications.
 * Stable design-to-palette rotation makes adjacent designs visibly different.
 */
export type BlueprintThreadPalette = Readonly<{
  name: string;
  primary: string;
  secondary: string;
  accent: string;
  outline: string;
}>;
const palette = (name: string, primary: string, secondary: string, accent: string, outline: string): BlueprintThreadPalette =>
  ({ name, primary, secondary, accent, outline });

export const BLUEPRINT_DARK_PALETTES: Readonly<Record<string, readonly BlueprintThreadPalette[]>> = {
  black: [
    palette("Graphite", "#343940", "#56606A", "#C4B9A8", "#191D22"),
    palette("Midnight Navy", "#25354A", "#45617B", "#C6B89A", "#111B29"),
    palette("Espresso", "#4A3029", "#785342", "#CFB898", "#291B19"),
    palette("Oxblood", "#58293A", "#8B4D59", "#D3BEAE", "#2C1823"),
    palette("Deep Forest", "#294638", "#5C7662", "#B7B294", "#182B22"),
  ],
  brown: [
    palette("Dark Cocoa", "#50352A", "#87614B", "#D7C4AA", "#2A1D18"),
    palette("Oxblood", "#5A2A39", "#845060", "#D6C5B1", "#301A24"),
    palette("Midnight", "#23354B", "#536B7E", "#D4C29F", "#162332"),
    palette("Olive", "#3A4733", "#697154", "#CFC39F", "#232A22"),
  ],
  "camo-green": [
    palette("Forest", "#254337", "#526D56", "#CEC49B", "#172A23"),
    palette("Graphite", "#343B3E", "#66716B", "#C2BAA6", "#1D2729"),
    palette("Earth", "#4E3C30", "#73624C", "#CEB79D", "#29251D"),
    palette("Oxblood", "#552C36", "#7C4B52", "#C2B79B", "#2C1E23"),
  ],
  charcoal: [
    palette("Slate", "#303D4A", "#526679", "#C7C8C1", "#1A222C"),
    palette("Midnight", "#202F4A", "#415674", "#C4B49C", "#131E31"),
    palette("Burgundy", "#542A39", "#885266", "#D0C0BA", "#2A1723"),
    palette("Espresso", "#4D342A", "#86604E", "#D3C4B0", "#291E1A"),
  ],
  "forest-green": [
    palette("Evergreen", "#244638", "#527463", "#C8C09D", "#172B22"),
    palette("Rust", "#663F30", "#946A4A", "#D6BA96", "#34231D"),
    palette("Charcoal", "#353B3A", "#64726C", "#D0C6A9", "#1D2421"),
    palette("Navy", "#26384D", "#52657A", "#D4CBAC", "#162333"),
  ],
  khaki: [
    palette("Espresso", "#50372A", "#816249", "#D4BBA0", "#2C211A"),
    palette("Deep Olive", "#404A35", "#757657", "#D2BC97", "#293121"),
    palette("Burgundy", "#632E40", "#95546A", "#DDC6B0", "#331E29"),
    palette("Denim", "#294258", "#5A7686", "#D2C7AE", "#1D2B38"),
  ],
  maroon: [
    palette("Oxblood", "#61293A", "#934F61", "#E0C4B4", "#311825"),
    palette("Charcoal", "#35353C", "#67656B", "#D6C8B8", "#222127"),
    palette("Mocha", "#51362C", "#82604A", "#DEC4A7", "#2D201B"),
    palette("Navy", "#263448", "#516478", "#D2C3AB", "#1A2634"),
  ],
  "mossy-oak-breakup": [
    palette("Moss", "#394836", "#6A785B", "#C9BF9E", "#202F22"),
    palette("Deep Cocoa", "#4F382B", "#82614C", "#CBB89F", "#2D211A"),
    palette("Slate", "#35424C", "#62737B", "#C6BDA5", "#222E36"),
    palette("Wine", "#5A303C", "#87606A", "#D6C3AC", "#311F25"),
  ],
  navy: [
    palette("Midnight", "#21314A", "#48617E", "#D2C29E", "#121F34"),
    palette("Steel", "#33495F", "#65819B", "#D5CDB6", "#1C2F43"),
    palette("Espresso", "#49372F", "#82624E", "#D4C5B3", "#2B221D"),
    palette("Burgundy", "#532B3E", "#855066", "#D8C4B0", "#2D1B2B"),
  ],
  "realtree-all-purpose": [
    palette("Woodland", "#3F4934", "#6F7456", "#CDC2A3", "#242D20"),
    palette("Mocha", "#563A30", "#886351", "#D9C5A7", "#2E221B"),
    palette("Charcoal", "#343B40", "#69716C", "#CFCCBC", "#1D2629"),
    palette("Burgundy", "#612F3F", "#8E5665", "#DBCCBB", "#301D29"),
  ],
  red: [
    palette("Dark Wine", "#662D3D", "#9B5360", "#E4C5AF", "#371A27"),
    palette("Graphite", "#34353C", "#66646B", "#D6C5B0", "#1D1E25"),
    palette("Mocha", "#54382F", "#846252", "#D7C5AA", "#30201C"),
    palette("Midnight", "#25344E", "#51647A", "#D8C8B4", "#1A2434"),
  ],
  royal: [
    palette("Navy", "#21334E", "#506C91", "#DAC8AB", "#172338"),
    palette("Slate", "#344A63", "#62839F", "#E2D4B8", "#1C3045"),
    palette("Graphite", "#363D46", "#6C7480", "#D3C9BA", "#232A33"),
    palette("Wine", "#592F46", "#8D5C70", "#D8C8B5", "#301E29"),
  ],
};

export const BLUEPRINT_DEFAULT_PALETTE = BLUEPRINT_DARK_PALETTES.black[0];

export function selectBlueprintThreadPalette(colorId: string, designOrdinal: number): BlueprintThreadPalette {
  const options = BLUEPRINT_DARK_PALETTES[colorId] ?? BLUEPRINT_DARK_PALETTES.black;
  const index = Number.isFinite(designOrdinal) ? Math.max(0, Math.trunc(designOrdinal)) : 0;
  return options[index % options.length];
}
export function blueprintPaletteRgb(hex: string): [number, number, number] {
  return [1, 3, 5].map(i => Number.parseInt(hex.slice(i, i + 2), 16)) as [number, number, number];
}
