import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("../../features/ai_operations", () => ({
  fetchAiOperationsDashboard: vi.fn(),
}));

import { JobQueuePage } from "./JobQueuePage";

describe("JobQueuePage", () => {
  it("renders the shared workspace sidebar with Job Queue active", () => {
    const markup = renderToStaticMarkup(<JobQueuePage />);

    expect(markup).toContain('class="ops-shell"');
    expect(markup).toContain('class="ops-sidebar"');
    expect(markup).toContain('href="/job-queue"');
    expect(markup).toContain('aria-current="page"');
    expect(markup).toContain("Asset Explorer");
    expect(markup).toContain("AI Operations");
    expect(markup).toContain("Access Management");
  });
});
