/** Explicit, per-profile/per-viewport quality budgets for fixture Browser QA.
 * Budgets are opt-in: keep non-calibrated metrics advisory rather than guessing limits.
 */
const METRICS = Object.freeze({
  maxUnnamedControls: "unnamedControls",
  maxMissingAlt: "missingAlt",
  maxSmallTargets: "smallTargets",
  maxViewportOverflowPx: "viewportOverflowPx",
  maxResourceCount: "resourceCount",
  maxResourceTransferKb: "resourceTransferKb",
  maxDomContentLoadedMs: "domContentLoadedMs",
});

export function checkQualityBudgets(quality, budgets = {}, state = "default") {
  if (!budgets || typeof budgets !== "object" || Array.isArray(budgets)) {
    throw new Error("Browser QA qualityBudgets must be an object");
  }
  const violations = [];
  for (const [limit, max] of Object.entries(budgets)) {
    const metric = METRICS[limit];
    if (!metric || typeof max !== "number" || !Number.isFinite(max) || max < 0) {
      throw new Error(`Invalid Browser QA budget ${limit}=${max}`);
    }
    const actual = quality[metric];
    if (typeof actual !== "number" || !Number.isFinite(actual) || actual < 0) {
      throw new Error(`Browser QA quality metric ${metric} is unavailable in ${state}`);
    }
    if (actual > max) violations.push({
      state, metric, actual, max,
      message: `${state}: ${metric} ${actual} exceeds budget ${max}`,
    });
  }
  return violations;
}

export function assertQualityBudgets(quality, budgets, state) {
  const violations = checkQualityBudgets(quality, budgets, state);
  if (violations.length) throw new Error(
    "Browser QA quality regression: " + violations.map(v => v.message).join("; "),
  );
}
