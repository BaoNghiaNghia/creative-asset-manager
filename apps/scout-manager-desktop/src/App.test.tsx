import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import App from "./App";
describe("Tauri Scout Manager dashboard", () => {
  it("renders both pipelines and monitoring UI without enabling native controls in browser preview", () => {
    const html = renderToStaticMarkup(<App />);
    expect(html).toContain("RRUGC Scout Manager");
    expect(html).toContain("Review Scout");
    expect(html).toContain("Keyword Scout");
    expect(html).toContain("Stage 0 keywords");
    expect(html).toContain("Total saved");
    expect(html).toContain("Added 24h");
    expect(html).toContain("Last saved");
    expect(html).toContain("Run automation");
    expect(html).toContain("Live activity");
    expect(html).toContain("Pairing");
    expect(html).toContain('disabled=""');
  });
  it("does not interpolate Scout tokens or synthetic running PIDs into server markup", () => {
    const html = renderToStaticMarkup(<App />);
    expect(html).not.toContain("RRUGC_SCOUT_TOKEN=");
    expect(html).not.toContain("22628");
    expect(html).toContain("Stopped");
  });
});
