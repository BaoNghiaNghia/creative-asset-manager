// @ts-expect-error Vitest resolves this Node-only CSS source import.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const explorer = readFileSync(new URL("../../styles/responsive-platform.css", import.meta.url), "utf8");
const shared = readFileSync(new URL("../../styles/ai-operations.css", import.meta.url), "utf8");
const access = readFileSync(new URL("../../styles/access-management.css", import.meta.url), "utf8");

function stylesFor(css: string, selector: string): Map<string, string> {
  const start = css.indexOf(selector + "{");
  expect(start, `Expected CSS selector ${selector}`).toBeGreaterThanOrEqual(0);
  const body = css.slice(start + selector.length + 1, css.indexOf("}", start));
  return new Map(body.split(";").map(part => part.trim()).filter(Boolean)
    .map(part => {
      const sep = part.indexOf(":");
      return [part.slice(0, sep).trim(), part.slice(sep + 1).trim()];
    }));
}

describe("Shared workspace sidebar visual sizing", () => {
  it("keeps Asset Explorer labels and row heights equal to AI Operations and Access Management", () => {
    const explorerLink = stylesFor(explorer, ".product-shell .sidebar .workspace-navigation a");
    const sharedLink = stylesFor(access, ".ops-sidebar .workspace-navigation a,.access-sidebar .workspace-navigation a");
    // Match the canonical rule, not the nested .ops-sidebar override above it.
    const sharedNavigation = shared.slice(shared.indexOf("/* Shared workspace sidebar navigation. */"));
    const sharedRow = stylesFor(sharedNavigation, ".workspace-navigation a");
    expect(explorerLink.get("font-size")).toBe(sharedLink.get("font-size"));
    expect(explorerLink.get("font-weight")).toBe(sharedLink.get("font-weight"));
    expect(explorerLink.get("min-height")).toBe(sharedRow.get("min-height"));
    expect(explorerLink.get("padding")).toBe(sharedRow.get("padding"));
    expect(explorerLink.get("gap")).toBe(sharedRow.get("gap"));

    const explorerSub = stylesFor(explorer, ".product-shell .sidebar .workspace-navigation .workspace-nav-submenu a");
    const sharedSub = stylesFor(shared, ".workspace-navigation .workspace-nav-submenu a");
    for (const property of ["font-size", "font-weight", "min-height", "padding", "gap"]) {
      expect(explorerSub.get(property)).toBe(sharedSub.get(property));
    }
  });

  it("keeps icon and expander dimensions consistent in all workspace menus", () => {
    const icon = stylesFor(explorer, ".product-shell .sidebar .workspace-navigation .workspace-nav-icon");
    const sharedIcon = stylesFor(shared, ".workspace-navigation .workspace-nav-icon");
    const subIcon = stylesFor(explorer, ".product-shell .sidebar .workspace-navigation .workspace-nav-submenu-icon");
    const sharedSubIcon = stylesFor(shared, ".workspace-navigation .workspace-nav-submenu-icon");
    const rrugcIcon = stylesFor(explorer, ".product-shell .sidebar .workspace-navigation .workspace-nav-rrugc-submenu .workspace-nav-submenu-icon");
    const rrugcSharedIcon = stylesFor(shared, ".workspace-navigation .workspace-nav-rrugc-submenu .workspace-nav-submenu-icon");

    for (const property of ["width", "height", "flex"]) {
      expect(icon.get(property)).toBe(sharedIcon.get(property));
      expect(subIcon.get(property)).toBe(sharedSubIcon.get(property));
      expect(rrugcIcon.get(property)).toBe(rrugcSharedIcon.get(property));
    }
    const expander = stylesFor(explorer, ".product-shell .sidebar .workspace-nav-expander");
    const sharedExpander = stylesFor(shared, ".workspace-navigation .workspace-nav-expander");
    expect(expander.get("width")).toBe(sharedExpander.get("width"));
    expect(expander.get("height")).toBe(sharedExpander.get("height"));
  });

  it("matches the common 16px brand title and 11px account label", () => {
    expect(stylesFor(explorer, ".product-shell .sidebar .brand strong").get("font-size")).toBe("16px");
    expect(stylesFor(explorer, ".product-shell .sidebar .brand small").get("font-size")).toBe("11px");
  });

  it("preserves the GatorHats submenu row sizes", () => {
    const gator = stylesFor(explorer, ".product-shell .sidebar .workspace-navigation .workspace-nav-rrugc-submenu a");
    const sharedGator = stylesFor(shared, ".workspace-navigation .workspace-nav-rrugc-submenu a");
    expect(gator.get("font-size")).toBe(sharedGator.get("font-size"));
    expect(gator.get("min-height")).toBe(sharedGator.get("min-height"));
  });
});