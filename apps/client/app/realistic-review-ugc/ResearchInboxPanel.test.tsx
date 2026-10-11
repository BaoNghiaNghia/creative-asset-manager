import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { parseResearchImport, ResearchInboxPanel } from "./ResearchInboxPanel";

describe("Research Intelligence Stage 0 Inbox", () => {
  it("renders as an opt-in, collapsed Stage 0 research tool", () => {
    const markup = renderToStaticMarkup(<ResearchInboxPanel />);
    expect(markup).toContain("Research Inbox");
    expect(markup).toContain("GatorHats Products");
    expect(markup).toContain('aria-expanded="false"');
    expect(markup).not.toContain("research-keyword-");
  });

  it("accepts only bounded, source-attributed records", () => {
    const signals = [{ source: "rss", channel: "hats", title: "Cap news",
      url: "https://news.google.com/a", excerpt: "Safe" }];
    expect(parseResearchImport(JSON.stringify(signals))).toHaveLength(1);
    expect(() => parseResearchImport("{}")).toThrow();
    expect(() => parseResearchImport("[]")).toThrow();
    expect(() => parseResearchImport(JSON.stringify([{source:"unknown",title:"a",url:"https://example.com"}]))).toThrow();
    expect(() => parseResearchImport(JSON.stringify(Array(51).fill(signals[0])))).toThrow();
    for (const [source, url] of [
      ["etsy", "https://www.etsy.com/listing/123456789/cap"],
      ["amazon", "https://amazon.com/dp/B012345678?tag=affiliate"],
      ["pinterest", "https://www.pinterest.com/pin/123456789/"],
    ]) {
      expect(parseResearchImport(JSON.stringify([{ source, channel: "manual", title: "Hats", url }]))).toHaveLength(1);
    }
    for (const [source, url] of [
      ["etsy", "https://www.etsy.com.evil.test/listing/123456789"],
      ["amazon", "https://www.amazon.com/s?k=cap"],
      ["pinterest", "https://www.etsy.com/listing/123456789"],
    ]) {
      expect(() => parseResearchImport(JSON.stringify([{ source, channel: "manual", title: "Hats", url }]))).toThrow();
    }
    for (const url of ["http://news.google.com/a", "https://localhost/", "https://127.0.0.1/",
      "https://evil.example@news.google.com/a", "javascript:alert(1)"]) {
      expect(() => parseResearchImport(JSON.stringify([{ ...signals[0], url }]))).toThrow();
    }
  });
});
