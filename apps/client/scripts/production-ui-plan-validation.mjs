/** Catch stale stage IDs before an authenticated Production Browser session. */
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SUPPORTED_ASSERTIONS = new Set([
  "visible",
  "width-ratio",
  "no-horizontal-overflow",
  "no-viewport-overflow",
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
