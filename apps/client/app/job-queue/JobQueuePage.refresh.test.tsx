// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchAiOperationsJobQueue, type AiOpsJob } from "../../features/ai_operations";
import { JobDurationCell, JobQueuePage } from "./JobQueuePage";

vi.mock("../../features/ai_operations", () => ({
  fetchAiOperationsJobQueue: vi.fn(),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const makeJob = (id: string, status = "completed"): AiOpsJob => ({
  id, job_type: "image_generate", entity_type: "asset", entity_id: id, asset_id: id,
  filename: id + ".png", source_thumbnail_url: null, generated_image_url: null,
  provider: "local", ai_model: null, status, priority: 0, attempt_count: 1, max_attempts: 5,
  processing_duration_ms: 1000, is_deferred: false, waiting_reason: null,
  next_attempt_at: null, claimed_at: status === "processing" ? "2026-11-12T13:00:00.000Z" : null,
  lease_expires_at: null, created_at: "2026-11-12T13:00:00.000Z",
  updated_at: "2026-11-12T13:00:00.000Z", completed_at: null, error: null,
});
type QueueResponse = Awaited<ReturnType<typeof fetchAiOperationsJobQueue>>;
function queueResponse(item: AiOpsJob): QueueResponse {
  return {
    jobs: { items: [item], total: 1, page: 1, page_size: 25 },
    summary: { queued: 0, running: 0, completed: 1, failed: 0 },
  } as unknown as QueueResponse;
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(r => { resolve = r; });
  return { promise, resolve };
}

let root: Root | undefined;
let host: HTMLDivElement | undefined;
async function mount(page: React.ReactNode) {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => root!.render(page));
}
afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  host?.remove();
  root = undefined; host = undefined;
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.mocked(fetchAiOperationsJobQueue).mockReset();
});

describe("Job Queue refresh and live duration isolation", () => {
  it("does not overwrite a newer filter result with a stale in-flight response", async () => {
    const old = deferred<QueueResponse>();
    const latest = deferred<QueueResponse>();
    const fetchQueue = vi.mocked(fetchAiOperationsJobQueue);
    fetchQueue.mockReturnValueOnce(old.promise).mockReturnValueOnce(latest.promise);
    await mount(<JobQueuePage />);
    expect(fetchQueue).toHaveBeenCalledTimes(1);
    expect(host!.querySelector(".cam-workspace-skeleton-rows")).not.toBeNull();

    const failedTab = [...host!.querySelectorAll<HTMLButtonElement>(".job-queue-tabs button")]
      .find(item => item.textContent === "Failed")!;
    await act(async () => failedTab.click());
    expect(fetchQueue).toHaveBeenCalledTimes(2);
    await act(async () => latest.resolve(queueResponse(makeJob("current-job"))));
    expect(host!.textContent).toContain("current-job.png");
    await act(async () => old.resolve(queueResponse(makeJob("stale-job"))));
    expect(host!.textContent).toContain("current-job.png");
    expect(host!.textContent).not.toContain("stale-job.png");
  });

  it("keeps the existing row visible during a manual refresh and a separate error", async () => {
    const refresh = deferred<QueueResponse>();
    const fetchQueue = vi.mocked(fetchAiOperationsJobQueue);
    fetchQueue.mockResolvedValueOnce(queueResponse(makeJob("kept-job")));
    fetchQueue.mockReturnValueOnce(refresh.promise);
    await mount(<JobQueuePage />);
    expect(host!.textContent).toContain("kept-job.png");

    const button = [...host!.querySelectorAll<HTMLButtonElement>(".job-queue-toolbar button")]
      .find(item => item.textContent?.includes("Refresh"))!;
    await act(async () => button.click());
    expect(host!.textContent).toContain("kept-job.png");
    expect(host!.textContent).toContain("Updating data");
    await act(async () => refresh.resolve(queueResponse(makeJob("new-job"))));
    expect(host!.textContent).toContain("new-job.png");
    expect(host!.textContent).not.toContain("kept-job.png");
  });

  it("one timer updates all running duration cells instead of rerendering the entire queue", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-12T13:00:01.000Z"));
    const spy = vi.spyOn(window, "setInterval");
    await mount(<>
      <JobDurationCell job={makeJob("running1", "processing")} />
      <JobDurationCell job={makeJob("running2", "processing")} />
      <JobDurationCell job={makeJob("done", "completed")} />
    </>);
    expect(spy).toHaveBeenCalledTimes(1);
    const first = host!.textContent;
    await act(async () => vi.advanceTimersByTime(1000));
    expect(host!.textContent).not.toEqual(first);
    expect(spy).toHaveBeenCalledTimes(1);
    await act(async () => root!.unmount());
    root = undefined;
    expect(vi.getTimerCount()).toBe(0);
  });
});
