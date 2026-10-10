import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { OutputActivityLoader } from "./OutputActivityLoader";

describe("shared three-dot output animation", () => {
  it("renders exactly three rotating dots and an accessible running state", () => {
    const html = renderToStaticMarkup(<OutputActivityLoader status="running" />);
    expect(html).toContain('aria-label="Generating output"');
    expect(html).toContain("Generating…");
    expect(html).toContain('class="rrugc-output-activity-orbit"');
    expect((html.match(/<span><\/span>/g) || []).length).toBe(3);
  });

  it("shows queued state and keeps the compact output slot icon-only", () => {
    const html = renderToStaticMarkup(<OutputActivityLoader status="queued" compact />);
    expect(html).toContain('aria-label="Output waiting in queue"');
    expect(html).toContain("is-compact");
    expect(html).not.toContain("Queued…");
    expect((html.match(/<span><\/span>/g) || []).length).toBe(3);
  });
});
