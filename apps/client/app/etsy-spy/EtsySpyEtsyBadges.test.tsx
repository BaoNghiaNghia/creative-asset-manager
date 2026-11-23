// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { EtsyDemandBadge } from "./EtsySpyPage";

describe("Etsy Spy source-derived Etsy-style badges", () => {
  it("renders only a verified source label as the golden Bestseller variant", () => {
    const html = renderToStaticMarkup(<EtsyDemandBadge label="Bestseller" />);
    expect(html).toContain('data-etsy-badge-kind="bestseller"');
    expect(html).toContain('aria-label="Etsy: Bestseller"');
    expect(html).toContain('class="etsy-demand-icon"');
    expect(html).toContain(">Bestseller</span>");
  });
  it("renders Popular now as a distinct source label without a Bestseller medal", () => {
    const html = renderToStaticMarkup(<EtsyDemandBadge label="Popular now" />);
    expect(html).toContain('data-etsy-badge-kind="popular"');
    expect(html).toContain(">Popular now</span>");
    expect(html).not.toContain('etsy-demand bestseller');
  });
  it("does not misrepresent an unknown or empty Etsy label as a bestseller", () => {
    const unknown = renderToStaticMarkup(<EtsyDemandBadge label="Rare find" />);
    expect(unknown).toContain('data-etsy-badge-kind="other"');
    expect(unknown).not.toContain('etsy-demand-icon');
    expect(unknown).toContain("Rare find");
    expect(renderToStaticMarkup(<EtsyDemandBadge label={null} />)).toContain("—");
  });
});
