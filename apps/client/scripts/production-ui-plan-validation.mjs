/** Catch stale stage IDs before an authenticated Production Browser session. */
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SUPPORTED_ASSERTIONS = new Set([
  "visible",
  "width-ratio",
  "no-horizontal-overflow",
  "no-viewport-overflow",
  "skill-upload-versions",
]);

export function validateProductionUiPlan(plan) {
  const issues = [];
  for (const route of plan?.routes || []) {
    for (const state of route.states || []) {
      for (const assertion of state.assertions || []) {
        if (!SUPPORTED_ASSERTIONS.has(assertion.type)) {
          issues.push(`${route.name}/${state.name}: unsupported Production UI assertion ${assertion.type}`);
        }
      }
      // Products and Reviews live in separate tablists, despite sharing one URL.
      // A selector can be valid and still be unreachable in the current workflow.
      if (String(route.path || "").startsWith("/realistic-review-ugc")) {
        const query = new URL(String(route.path), "https://cam-qa.invalid").searchParams;
        const workflow = query.get("workflow") === "reviews" ? "reviews" : "products";
        const allowed = workflow === "reviews"
          ? new Set(["reviews-stage2", "stage3", "stage4", "stage5"])
          : new Set(["stage0", "stage1", "stage2"]);
        const targetTab = String(state.click || "").match(/^#rrugc-tab-(reviews-stage2|stage[0-5])$/)?.[1];
        if (targetTab && !allowed.has(targetTab)) {
          issues.push(`${route.name}/${state.name}: ${targetTab} is not visible in ${workflow} workflow`);
        }
      }
      const selectedStage = String(state.click || "").match(/^#rrugc-tab-stage(\d+)$/)?.[1];
      if (selectedStage) {
        for (const assertion of state.assertions || []) {
          for (const field of ["selector", "relativeTo"]) {
            const target = String(assertion[field] || "").match(/^#rrugc-panel-stage(\d+)$/);
            if (target && target[1] !== selectedStage) {
              issues.push(`${route.name}/${state.name}: tab Stage ${selectedStage} targets panel Stage ${target[1]} (${field})`);
            }
          }
        }
      }
    }
  }
  return issues;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const file = process.argv[2];
  if (!file) throw new Error("Provide a Production UI plan JSON file.");
  const plan = JSON.parse(await fs.readFile(file, "utf8"));
  const issues = validateProductionUiPlan(plan);
  for (const issue of issues) console.error(`Invalid Production UI plan: ${issue}`);
  if (issues.length) process.exitCode = 2;
  else console.log("Production UI plan stage bindings are valid.");
}
