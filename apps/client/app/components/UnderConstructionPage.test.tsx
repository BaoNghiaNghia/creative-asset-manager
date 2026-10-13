import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { UnderConstructionPage } from "./UnderConstructionPage";

describe("UnderConstructionPage", () => {
  it("uses the supplied construction video and a no-motion fallback", () => {
    const html = renderToStaticMarkup(createElement(UnderConstructionPage));
    expect(html).toContain('src="/under-construction/animation.mp4"');
    expect(html).toContain('poster="/under-construction/poster.jpg"');
    expect(html).toContain('autoplay=""');
    expect(html).toContain('muted=""');
    expect(html).toContain('loop=""');
    expect(html).toContain('playsinline=""');
    expect(html).toContain("Under Construction");
    expect(html).toContain('href="/"');
    expect(html).not.toContain("data:video/");
  });
});
