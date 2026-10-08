import type { Stage3ReviewGroup, Stage3ReviewGroupList } from "./types";

function statusForGroup(group: Stage3ReviewGroup): Stage3ReviewGroup["status"] {
  if (group.analyzing_count) return "analyzing";
  if (group.image_count > 0 && group.ready_count === group.image_count) return "ready";
  if (group.ready_count) return "partial";
  if (group.image_count > 0 && group.rejected_count === group.image_count) return "rejected";
  if (group.error_count && !group.pending_count) return "error";
  return "pending";
}

// Pagination boundaries can split one Drive folder across two pages.
// Merge by immutable Stage 4 job ID and recompute counts, never duplicate images.
export function mergeStage5Pages(
  previous: Stage3ReviewGroupList,
  page: Stage3ReviewGroupList,
): Stage3ReviewGroupList {
  const groups = new Map(previous.items.map(group => [group.folder_id, group]));
  for (const next of page.items) {
    const existing = groups.get(next.folder_id);
    if (!existing) {
      groups.set(next.folder_id, next);
      continue;
    }
    const byJobId = new Map(existing.images.map(image => [image.stage2_job_id, image]));
    for (const image of next.images) {
      if (!byJobId.has(image.stage2_job_id)) byJobId.set(image.stage2_job_id, image);
    }
    const images = [...byJobId.values()];
    const ready_count = images.filter(image => image.analysis_status === "ready").length;
    const rejected_count = images.filter(image => image.analysis_status === "rejected").length;
    const analyzing_count = images.filter(image =>
      image.analysis_status === "queued" || image.analysis_status === "analyzing").length;
    const error_count = images.filter(image => image.analysis_status === "error").length;
    const pending_count = images.length - ready_count - rejected_count - analyzing_count - error_count;
    const merged: Stage3ReviewGroup = {
      ...existing,
      images,
      image_count: images.length,
      ready_count,
      rejected_count,
      analyzing_count,
      pending_count,
      error_count,
      latest_completed_at:
        [existing.latest_completed_at, next.latest_completed_at].filter(
          (item): item is string => Boolean(item),
        ).sort().at(-1) || null,
    };
    merged.status = statusForGroup(merged);
    groups.set(merged.folder_id, merged);
  }
  const items = [...groups.values()];
  const sum = (field: "ready_count" | "rejected_count" | "analyzing_count" | "pending_count" | "error_count") =>
    items.reduce((acc, group) => acc + group[field], 0);
  return {
    items,
    total_groups: items.length,
    total_images: items.reduce((acc, group) => acc + group.image_count, 0),
    ready_images: sum("ready_count"),
    rejected_images: sum("rejected_count"),
    analyzing_images: sum("analyzing_count"),
    pending_images: sum("pending_count"),
    error_images: sum("error_count"),
    has_more: page.has_more,
    next_cursor: page.next_cursor || null,
  };
}
