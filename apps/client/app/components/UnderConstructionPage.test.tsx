import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { UnderConstructionPage } from "./UnderConstructionPage";

describe("UnderConstructionPage", () => {
  it("uses media URLs distinct from the /under-construction page route", () => {
    const html = renderToStaticMarkup(createElement(UnderConstructionPage));
    expect(html).toContain('src="/construction-media/animation.mp4"');
    expect(html).toContain('poster="/construction-media/poster.jpg"');
    expect(html).not.toContain('src="/under-construction/');
  });

  it("uses the supplied construction video and a no-motion fallback", () => {
    const html = renderToStaticMarkup(createElement(UnderConstructionPage));
    expect(html).toContain('src="/construction-media/animation.mp4"');
    expect(html).toContain('poster="/construction-media/poster.jpg"');
    expect(html).toContain('autoplay=""');
    expect(html).toContain('muted=""');
    expect(html).toContain('loop=""');
    expect(html).toContain('playsinline=""');
    expect(html).toContain("Under Construction");
    expect(html).toContain('class="brand-logo"');
    expect(html).toContain('class="cam-under-brand-mark"');
    expect(html).toContain('class="cam-under-button"');
    expect(html).toContain('class="cam-under-back-icon"');
    expect(html).toContain('href="/"');
    expect(html).not.toContain("data:video/");
  });
});
