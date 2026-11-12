// @vitest-environment jsdom
// @ts-expect-error Node fs is used only by Vitest, not the production bundle.
import { readFileSync } from "node:fs";
// @ts-expect-error Vitest executes this test-only Node import.
import { resolve } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AiOperationsShell } from "./ai-operations/AiOperationsPage";
import { BrandingDnaPage } from "./branding-dna/BrandingDnaPage";
import { TiktokShopScoutUgcPage } from "./tiktokshop/TiktokShopScoutUgcPage";
const css = readFileSync(resolve(process.cwd(), "styles/cam-platform-evo.css"), "utf8");
const main = readFileSync(resolve(process.cwd(), "app/main.tsx"), "utf8");

describe("evondevKit phase 5 — four independent operational workspaces", () => {
  it("loads the new adapter after the prior shared token and console layers", () => {
    expect(main.indexOf('import "../styles/cam-platform-evo.css"'))
      .toBeGreaterThan(main.indexOf('import "../styles/cam-console-evo.css"'));
    expect(main).toContain('import "../app/styles/cam-evo-tokens.css"');
  });

  it("scopes styling to the four intended root classes, not global app controls", () => {
    for (const selector of [
      ".cam-evo-ai-operations .ops-tabs-carousel",
      ".cam-evo-ai-operations .ops-data-table",
      ".branding-dna-page .bdna-stage-shell",
      ".branding-dna-page .bdna-header-runtime",
      ".rrugc-shell.rrugc-tiktokshop-workspace .rrugc-source-plan-table",
      ".rrugc-shell.rrugc-tiktokshop-workspace .rrugc-stage3-gallery",
      ".review-board-shell .review-board-workspace",
      ".review-board-shell .rrugc-review-decision",
    ]) expect(css).toContain(selector);
    expect(css).not.toMatch(/^\s*\.(?:etsy|rrugc-evo-products|rrugc-evo-reviews)[\w.-]/m);
    expect(css).not.toMatch(/^\s*(?:button|main|table|article|body|:root)\s*\{/m);
    expect(css).not.toContain("pointer-events: none");
    expect(css).toContain(":focus-visible");
    expect(css).toContain("prefers-reduced-motion");
  });

  it("preserves all screen identities and stage navigation in existing React markup", () => {
    const ops = renderToStaticMarkup(<AiOperationsShell><p>panel</p></AiOperationsShell>);
    expect(ops).toContain("ops-shell cam-evo-ai-operations");
    expect(ops).toContain("panel");
    const dna = renderToStaticMarkup(<BrandingDnaPage />);
    expect(dna).toContain("branding-dna-page");
    expect(dna).toContain('aria-label="Branding DNA stages"');
    expect(dna.match(/role="tab" aria-selected=/g)).toHaveLength(6);
    expect(dna).toContain("Scanner not connected");
    const tiktok = renderToStaticMarkup(<TiktokShopScoutUgcPage />);
    expect(tiktok).toContain("rrugc-tiktokshop-workspace");
    expect(tiktok).toContain('id="tiktok-tab-stage3"');
    expect(tiktok).toContain('id="tiktok-tab-stage4"');
    expect(tiktok).toContain('id="tiktok-tab-stage5"');
    expect(tiktok).toContain("Internal apparel image QA only");
  });
});
