import { describe, expect, it } from "vitest";
// @ts-expect-error Test-only Node fs import; Vitest executes this in Node.
import { readFileSync } from "node:fs";
const consoleCss = readFileSync(new URL("../styles/cam-console-evo.css", import.meta.url), "utf8");
import appSource from "./App.tsx?raw";
import mainSource from "./main.tsx?raw";

describe("evondevKit phase 4 CSS isolation", () => {
  it("loads the scoped console adapter after the legacy stylesheet stack", () => {
    const legacyImport = mainSource.indexOf('import "../styles/rrugc-sidebar-navigation.css"');
    const adapterImport = mainSource.indexOf('import "../styles/cam-console-evo.css"');
    expect(adapterImport).toBeGreaterThan(legacyImport);
    expect(mainSource).toContain('import "../app/styles/cam-evo-tokens.css"');
    expect(appSource).toContain('"cam-evo-explorer"');
  });

  it("keeps the four operational consoles in separate root selectors", () => {
    for (const selector of [
      ".cam-evo-explorer .grid article",
      ".cam-evo-explorer .explorer-content",
      ".job-queue-shell .job-queue-table-scroll",
      ".job-queue-shell .job-queue-tabs button",
      ".inventory-workspace .inventory-pipeline-scroll",
      ".inventory-workspace .ops-inventory-summary",
      ".access-shell .access-table-wrap",
      ".access-shell .access-member-dialog",
    ]) expect(consoleCss).toContain(selector);
    // Existing GatorHats/Etsy styles must not be globally overridden.
    expect(consoleCss).not.toMatch(/^\s*\.(?:rrugc|etsy)[\w.-]/m);
    expect(consoleCss).not.toMatch(/^\s*(?:button|main|table|aside|body|article|:root)\s*\{/m);
  });

  it("preserves existing interactions by avoiding control-hiding or global reset", () => {
    expect(consoleCss).not.toMatch(/display\s*:\s*none/);
    expect(consoleCss).not.toMatch(/pointer-events\s*:\s*none/);
    expect(consoleCss).not.toMatch(/window\.location|location\.reload|setInterval/);
    expect(consoleCss).toContain(":focus-visible");
    expect(consoleCss).toContain("prefers-reduced-motion");
  });
});
