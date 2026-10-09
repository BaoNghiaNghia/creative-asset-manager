// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SkillJobLogDialog } from "./SkillJobLogDialog";
import { getSkillJobLog } from "./api";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
vi.mock("./api", async importOriginal => ({
  ...(await importOriginal<typeof import("./api")>()),
  getSkillJobLog: vi.fn(),
}));
afterEach(() => { document.body.replaceChildren(); vi.clearAllMocks(); });

describe("Codex live execution progress", () => {
  it("shows bounded event metadata and not raw model messages", async () => {
    vi.mocked(getSkillJobLog).mockResolvedValueOnce({
      stage: "stage1", job_id: "job-1", status: "running",
      skill: { name: "hanh-redesign-8869-ver-4", version: "4.0.0", source: "local" },
      attempts: [{
        id: "process-1", status: "running", attempt_count: 1, max_attempts: 3,
        duration_ms: 0, error_code: null, error_message: "",
        created_at: "2026-10-09T08:00:00Z", updated_at: "2026-10-09T08:10:00Z",
        completed_at: null,
        execution: {
          state: "running", started_at: "2026-10-09T08:00:00Z",
          last_activity_at: "2026-10-09T08:10:00Z", elapsed_seconds: 610,
          event_count: 1250, last_event: "item.completed:tool_call",
          stdout_bytes: 8_192_000, stderr_bytes: 0,
          events: [{ time: "2026-10-09T08:10:00Z", event: "item.completed", item: "tool_call" }],
        },
      }],
    });
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<SkillJobLogDialog stage="stage1" jobId="job-1" onClose={() => undefined} />));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(host.textContent).toContain("Codex · running · 10m 10s");
    expect(host.textContent).toContain("1250 events");
    expect(host.textContent).toContain("8000 KB streamed");
    expect(host.textContent).toContain("Recent execution events");
    await act(async () => root.unmount());
  });
});
