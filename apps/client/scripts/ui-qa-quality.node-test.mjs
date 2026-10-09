import assert from "node:assert/strict";
import test from "node:test";
import { checkQualityBudgets, assertQualityBudgets } from "./ui-qa-quality.mjs";

const metrics = {
  unnamedControls: 0, missingAlt: 0, smallTargets: 0,
  viewportOverflowPx: 0, resourceCount: 40,
  resourceTransferKb: 500, domContentLoadedMs: 390,
};

test("explicit quality budgets pass at their limits and remain opt-in", () => {
  assert.deepEqual(checkQualityBudgets(metrics, {}, "stage1-actions"), []);
  assert.doesNotThrow(() => assertQualityBudgets(metrics, {
    maxViewportOverflowPx: 0, maxUnnamedControls: 0, maxMissingAlt: 0,
    maxSmallTargets: 0, maxResourceCount: 40,
  }, "stage1-actions"));
});

test("visible accessibility, touch and overflow regressions fail with context", () => {
  const failing = { ...metrics, unnamedControls: 1, missingAlt: 2,
    viewportOverflowPx: 5, smallTargets: 3 };
  const budget = { maxViewportOverflowPx: 0, maxUnnamedControls: 0,
    maxMissingAlt: 0, maxSmallTargets: 0 };
  const issues = checkQualityBudgets(failing, budget, "stage2-actions");
  assert.equal(issues.length, 4);
  assert.ok(issues.every(issue => issue.state === "stage2-actions"));
  assert.throws(() => assertQualityBudgets(failing, budget, "stage2-actions"),
    /quality regression.*viewportOverflowPx 5 exceeds budget 0/);
});

test("invalid budget configuration cannot silently pass", () => {
  assert.throws(() => checkQualityBudgets(metrics, { imaginaryMetric: 0 }), /Invalid Browser QA budget/);
  assert.throws(() => checkQualityBudgets(metrics, { maxResourceCount: -3 }), /Invalid Browser QA budget/);
  assert.throws(() => checkQualityBudgets(metrics, { maxMissingAlt: "0" }), /Invalid Browser QA budget/);
  assert.throws(() => checkQualityBudgets({}, { maxMissingAlt: 0 }), /unavailable/);
});
