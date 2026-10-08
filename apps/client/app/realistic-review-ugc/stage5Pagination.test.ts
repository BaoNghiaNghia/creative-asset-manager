import { describe, expect, it } from "vitest";
import { mergeStage5Pages } from "./stage5Pagination";
import type {
  Stage3ReviewGroup,
  Stage3ReviewGroupList,
  Stage3ReviewImage,
  Stage3AnalysisStatus,
} from "./types";

const image = (id: string, analysis_status: Stage3AnalysisStatus = "pending") =>
  ({ stage2_job_id: id, analysis_status } as Stage3ReviewImage);

function group(folderId: string, images: Stage3ReviewImage[]): Stage3ReviewGroup {
  return {
    folder_id: folderId,
    folder_name: folderId,
    folder_path: folderId,
    image_count: images.length,
    status: "pending",
    ready_count: images.filter(item => item.analysis_status === "ready").length,
    rejected_count: images.filter(item => item.analysis_status === "rejected").length,
    analyzing_count: images.filter(item =>
      item.analysis_status === "queued" || item.analysis_status === "analyzing").length,
    error_count: images.filter(item => item.analysis_status === "error").length,
    pending_count: images.filter(item => item.analysis_status === "pending").length,
    latest_completed_at: null,
    images,
  };
}

function page(items: Stage3ReviewGroup[], nextCursor: string | null): Stage3ReviewGroupList {
  const sum = (field: "ready_count" | "rejected_count" | "analyzing_count" | "pending_count" | "error_count") =>
    items.reduce((total, item) => total + item[field], 0);
  return {
    items,
    total_groups: items.length,
    total_images: items.reduce((total, item) => total + item.image_count, 0),
    ready_images: sum("ready_count"),
    rejected_images: sum("rejected_count"),
    analyzing_images: sum("analyzing_count"),
    pending_images: sum("pending_count"),
    error_images: sum("error_count"),
    has_more: Boolean(nextCursor),
    next_cursor: nextCursor,
  };
}

describe("Stage 5 cursor pagination", () => {
  it("merges a folder split across page boundaries without duplicating jobs", () => {
    const first = page([group("folder-a", [image("a"), image("b")])], "b");
    const second = page([
      group("folder-a", [image("b"), image("c", "ready")]),
      group("folder-b", [image("d", "error")]),
    ], null);
    const merged = mergeStage5Pages(first, second);
    expect(merged.total_groups).toBe(2);
    expect(merged.total_images).toBe(4);
    expect(merged.ready_images).toBe(1);
    expect(merged.error_images).toBe(1);
    expect(merged.items[0].images.map(item => item.stage2_job_id)).toEqual(["a", "b", "c"]);
    expect(merged.has_more).toBe(false);
    expect(merged.next_cursor).toBeNull();
  });

  it("preserves loaded history and updates statuses when the newest page refreshes", () => {
    const previouslyLoaded = page([
      group("folder-a", [image("recent", "pending"), image("old", "pending")]),
      group("folder-b", [image("archived", "ready")]),
    ], "archived");
    const latest = page([group("folder-a", [image("new", "ready"), image("recent", "ready")])], "recent");
    // The latest page is first: its newer status wins on overlapping IDs.
    const merged = mergeStage5Pages(latest, previouslyLoaded);
    expect(merged.items[0].images.map(item => item.stage2_job_id)).toEqual(["new", "recent", "old"]);
    expect(merged.items[0].ready_count).toBe(2);
    expect(merged.items[0].pending_count).toBe(1);
    expect(merged.items[1].folder_id).toBe("folder-b");
    expect(merged.next_cursor).toBe("archived");
  });

  it("retains all 5,251 images across small pages and retries with overlapping results", () => {
    const count = 5251;
    const pageSize = 250;
    let collected = page([group("folder-a",
      Array.from({ length: pageSize }, (_, n) => image(`job-${n}`)))], "job-249");
    for (let offset = pageSize; offset < count; offset += pageSize) {
      const images = Array.from({ length: Math.min(pageSize, count - offset) },
        (_, n) => image(`job-${offset + n}`));
      if (offset === pageSize) images.unshift(image("job-249"));
      const lastPage = offset + pageSize >= count;
      collected = mergeStage5Pages(collected,
        page([group("folder-a", images)], lastPage ? null : images.at(-1)!.stage2_job_id));
    }
    expect(collected.total_groups).toBe(1);
    expect(collected.total_images).toBe(count);
    expect(new Set(collected.items[0].images.map(item => item.stage2_job_id)).size).toBe(count);
    expect(collected.has_more).toBe(false);
    expect(collected.next_cursor).toBeNull();
  });
});
