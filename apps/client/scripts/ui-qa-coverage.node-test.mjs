import assert from "node:assert/strict";
import test from "node:test";
import { analyzeBaselineCoverage, collectVisualCoverage } from "./ui-qa-coverage.mjs";

test("baseline drift identifies newly added stage states without overwriting baselines", () => {
  const plan = { viewports: ["desktop", "mobile"], steps: [
    { name: "default" }, { name: "stage1-actions" }, { name: "stage2-actions" },
  ] };
  const manifest = { states: ["default"], viewports: [{ name: "desktop" }] };
  const result = analyzeBaselineCoverage(plan, manifest, "realistic-review-ugc");
  assert.equal(result.status, "stale");
  assert.deepEqual(result.missingStates, ["stage1-actions", "stage2-actions"]);
  assert.deepEqual(result.missingViewports, ["mobile"]);
});

test("baseline audit returns a report for all registered profiles", async () => {
  const report = await collectVisualCoverage();
  assert.ok(report.summary.total >= 9);
  assert.equal(report.summary.total, report.profiles.length);
  assert.ok(report.profiles.some(p => p.profile === "realistic-review-ugc"));
  assert.ok(report.summary.aligned + report.summary.stale === report.summary.total);
});
