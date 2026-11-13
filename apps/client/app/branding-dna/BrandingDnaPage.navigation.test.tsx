// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./BrandingDnaFoundation", () => ({ BrandingDnaFoundation: () => <div>Foundation</div> }));
vi.mock("./BrandingDnaWorkers", () => ({ BrandingDnaWorkers: () => <div>Workers</div> }));
vi.mock("./BrandingDnaEvidence", () => ({ BrandingDnaEvidence: () => <div>Evidence</div> }));
vi.mock("./BrandingDnaKnowledge", () => ({ BrandingDnaKnowledge: () => <div>Knowledge</div> }));
vi.mock("./BrandingDnaReadiness", () => ({ BrandingDnaReadiness: () => <div>Readiness</div> }));

import { BrandingDnaPage } from "./BrandingDnaPage";

afterEach(() => {
  window.history.replaceState(null, "", "/branding-dna");
  vi.unstubAllGlobals();
});

describe("Branding DNA stage navigation", () => {
  it("updates a shareable URL without reloading and restores stage on Back/Forward", async () => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    window.history.replaceState(null, "", "/branding-dna");

    const container = document.createElement("div");
    const root = createRoot(container);
    await act(async () => root.render(<BrandingDnaPage />));
    const current = () => container.querySelector<HTMLButtonElement>('[role="tab"][aria-selected="true"]');

    expect(current()?.id).toBe("bdna-stage-overview");
    await act(async () => container.querySelector<HTMLButtonElement>("#bdna-stage-knowledge")?.click());
    expect(window.location.pathname).toBe("/branding-dna/knowledge");
    expect(current()?.id).toBe("bdna-stage-knowledge");

    await act(async () => container.querySelector<HTMLButtonElement>("#bdna-stage-shops")?.click());
    expect(window.location.pathname).toBe("/branding-dna/shops");
    expect(current()?.id).toBe("bdna-stage-shops");

    await act(async () => {
      window.history.replaceState(null, "", "/branding-dna/knowledge");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(current()?.id).toBe("bdna-stage-knowledge");
    expect(container.querySelector("#bdna-panel-knowledge")).toBeTruthy();
    await act(async () => root.unmount());
  });
});
