import type { ColorwayJob, EcommerceJob } from "./api";
import { PHOTO_COLOR_KEYS } from "./ProductsPhotoWorkflow";

/** Exact 10 per-slot outputs. A scene batch is not complete unless all 3
 * persisted child jobs have completed and their output links exist. */
export const PRODUCT_STAGE2_PHOTO_STAGES = [
  "prod_s2_ec1", "prod_s2_ec2", "prod_s2_ec3", "prod_s2_ec4",
  "prod_s2_mh_1", "prod_s2_mh_2", "prod_s2_mh_3",
  "prod_s2_ugc_1", "prod_s2_ugc_2", "prod_s2_ugc_3",
] as const;

export const PRODUCT_STAGE2_TOTAL_OUTPUTS = PRODUCT_STAGE2_PHOTO_STAGES.length + PHOTO_COLOR_KEYS.length;

export type DesignProgressState =
  "checking" | "unavailable" | "not-started" | "partial" | "running" | "failed" | "complete";

export type ProductDesignProgress = {
  state: DesignProgressState;
  completed: number;
  total: number;
  running: number;
  failed: number;
};

type JobSnapshot = "loading" | "ready" | "unavailable";

const COLOR_JOB_KEYS: ReadonlySet<string> = new Set(PHOTO_COLOR_KEYS);
const PHOTO_JOB_KEYS: ReadonlySet<string> = new Set(PRODUCT_STAGE2_PHOTO_STAGES);

export function productDesignProgress(
  jobs: EcommerceJob[],
  colorways: ColorwayJob[],
  selectedColor: string,
  snapshot: JobSnapshot = "ready",
): ProductDesignProgress {
  const empty = { completed: 0, total: PRODUCT_STAGE2_TOTAL_OUTPUTS, running: 0, failed: 0 };
  if (snapshot !== "ready") {
    return { ...empty, state: snapshot === "loading" ? "checking" : "unavailable" };
  }

  const photos = new Map(
    jobs.filter(job => PHOTO_JOB_KEYS.has(job.stage) && (job.color_key || "natural-black") === selectedColor)
      .map(job => [job.stage, job] as const),
  );
  const colors = new Map(
    colorways.filter(job => COLOR_JOB_KEYS.has(job.color_key)).map(job => [job.color_key, job] as const),
  );
  let completed = 0;
  let running = 0;
  let failed = 0;
  const count = (job: EcommerceJob | ColorwayJob | undefined) => {
    if (job?.status === "completed" && job.output_url) completed++;
    else if (job?.status === "failed") failed++;
    else if (job?.status === "queued" || job?.status === "running") running++;
  };
  PRODUCT_STAGE2_PHOTO_STAGES.forEach(stage => count(photos.get(stage)));
  PHOTO_COLOR_KEYS.forEach(color => count(colors.get(color)));
  const state: DesignProgressState =
    completed === PRODUCT_STAGE2_TOTAL_OUTPUTS ? "complete"
    : failed ? "failed"
    : running ? "running"
    : completed ? "partial"
    : "not-started";
  return { state, completed, total: PRODUCT_STAGE2_TOTAL_OUTPUTS, running, failed };
}

export const productProgressLabel: Record<DesignProgressState, string> = {
  checking: "Checking",
  unavailable: "Unavailable",
  "not-started": "Not started",
  partial: "Partial",
  running: "Running",
  failed: "Failed",
  complete: "Complete",
};
