# Embroidery Thread Policy

`mauchi.txt` is the **single source of truth** for allowed thread codes, names, and RGB screen-reference values. Never invent a thread code or use a code absent from that file.

## Selection priority

1. Valid user-specified thread code/color.
2. Preserve the current artwork's intended color relationships and map required colors to sensible available threads in `mauchi.txt`.
3. Maintain the same front-art palette across the set.
4. Change a front-art color only when necessary for legibility/contrast or when explicitly requested.
5. **Do not force the primary front artwork color to match the bill.**

RGB values are visual references, not guarantees of physical thread appearance. Prefer semantic color family, visual similarity, contrast, and design intent over raw Euclidean RGB extracted from photographed hats.

## Front artwork

- Preserve intended artwork colors where practical.
- Simplify only micro-detail that cannot plausibly stitch.
- Do not collapse meaningful distinct colors just to reduce palette size.
- Once selected, keep codes consistent across ecommerce, multi-cap, and UGC outputs for the same job unless a specific variant is intentionally defined.

## Side personalization default

When enabled and the user does not choose its thread color, coordinate side text with the **bill** using this curated map:

| Bill / pattern | Default code | Thread name |
|---|---:|---|
| Black | 1800 | Black |
| Brown | 1859 | Dark Brown |
| Camo Green | 1706 | Fresh Green |
| Charcoal | 1841 | Dark Gray |
| Forest Green | 1750 | Emerald Green |
| Khaki | 1939 | Khaki |
| Maroon | 1784 | Maroon |
| Mossy Oak Breakup | 1859 | Dark Brown |
| Navy | 1967 | Navy |
| Realtree All Purpose | 1859 | Dark Brown |
| Red | 1637 | Red |
| Royal | 1829 | Blue |

A valid explicit user choice overrides this map.

## Contrast fallback

If a selected thread becomes unreadable against the actual panel:
- preserve artwork identity first;
- choose the nearest sensible valid inventory alternative with adequate contrast;
- do not invent outlines, shadows, or extra borders unless part of the artwork or explicitly requested.

Useful neutral fallbacks when appropriate:
- light crown: 1800 Black;
- dark area: 1801 White, 1682 Linen, or 1738 Beige.
