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

describe("Skill execution timeline", () => {
  const data = {
    stage: "stage1" as const, job_id: "job-retry", status: "queued",
    skill: { name: "hanh-redesign-8869-ver-4", version: null, source: "local" },
    attempts: [
      {
        id: "retry-attempt", status: "pending", attempt_count: 0, max_attempts: 3,
        duration_ms: 8_000, error_code: "codex_image_usage_limited",
        error_message: "Codex image generation usage is temporarily limited.",
        created_at: "2026-10-09T10:22:20Z", updated_at: "2026-10-09T10:22:28Z",
        completed_at: null,
        execution: {
          state: "cli_failed", started_at: "2026-10-09T10:22:20Z",
          last_activity_at: "2026-10-09T10:22:28Z", elapsed_seconds: 8,
          event_count: 4, last_event: "turn.failed", stdout_bytes: 1024,
          stderr_bytes: 0, events: [
            { time: "2026-10-09T10:22:20Z", event: "thread.started", item: "" },
            { time: "2026-10-09T10:22:28Z", event: "turn.failed", item: "" },
          ],
        },
      },
      {
        id: "completed-attempt", status: "completed", attempt_count: 1, max_attempts: 3,
        duration_ms: 71_000, error_code: null, error_message: "",
        created_at: "2026-10-09T08:48:00Z", updated_at: "2026-10-09T08:49:11Z",
        completed_at: "2026-10-09T08:49:11Z", execution: null,
      },
    ],
  };

  it("separates queued job, pending retry, historical completion and Codex error", async () => {
    vi.mocked(getSkillJobLog).mockResolvedValueOnce(data);
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<SkillJobLogDialog stage="stage1" jobId="job-retry" onClose={() => undefined} />));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(host.querySelectorAll(".rrugc-log-attempt")).toHaveLength(2);
    expect(host.querySelector(".rrugc-log-summary")?.textContent).toContain("Queued");
    expect(host.querySelector(".rrugc-log-summary")?.textContent).toContain("1 completed");
    expect(host.textContent).toContain("Run 2");
    expect(host.textContent).toContain("Retry pending");
    expect(host.textContent).toContain("codex_image_usage_limited");
    expect(host.textContent).toContain("Codex image generation usage is temporarily limited.");
    expect(host.textContent).toContain("Run 1");
    expect(host.querySelector(".rrugc-log-attempt.is-success")?.textContent).toContain("Completed");
    const details = host.querySelector("details");
    expect(details?.open).toBe(false);
    expect(details?.textContent).toContain("Recent execution events");
    expect(host.querySelector('button[aria-label="Refresh job logs"]')).not.toBeNull();
    expect(host.querySelector('button[aria-label="Close job logs"]')).not.toBeNull();
    await act(async () => root.unmount());
  });

  it("manual refresh updates status without clearing older attempts", async () => {
    vi.mocked(getSkillJobLog)
      .mockResolvedValueOnce(data)
      .mockResolvedValueOnce({ ...data, status: "running" });
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<SkillJobLogDialog stage="stage1" jobId="job-retry" onClose={() => undefined} />));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    await act(async () => {
      host.querySelector<HTMLButtonElement>('button[aria-label="Refresh job logs"]')!.click();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(getSkillJobLog).toHaveBeenCalledTimes(2);
    expect(host.querySelector(".rrugc-log-summary")?.textContent).toContain("Running");
    expect(host.querySelectorAll(".rrugc-log-attempt")).toHaveLength(2);
    await act(async () => root.unmount());
  });
});
