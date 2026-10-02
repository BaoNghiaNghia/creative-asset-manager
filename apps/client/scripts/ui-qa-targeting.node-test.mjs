import assert from "node:assert/strict";
import test from "node:test";
import {
  collectRepairTargets,
  parseCsvList,
  sanitizeStateName,
  selectExecutionSteps,
  selectPlanSteps,
} from "./ui-qa-targeting.mjs";

test("parses compact CSV targets", () => {
  assert.deepEqual(parseCsvList(" mobile, desktop ,,tabletPortrait "), [
    "mobile",
    "desktop",
    "tabletPortrait",
  ]);
});

test("selects only requested plan states in requested order", () => {
  const steps = [
    { name: "default" },
    { name: "hover-card" },
    { name: "selected-hover" },
  ];
  assert.deepEqual(selectPlanSteps(steps, ["selected-hover", "default"]), [
    steps[2],
    steps[0],
  ]);
  assert.throws(
    () => selectPlanSteps(steps, ["missing"]),
    /Unknown UI QA state/,
  );
});

test("normalizes state names consistently", () => {
  assert.equal(sanitizeStateName("Selected + Hover"), "Selected-Hover");
});

test("executes prerequisite states without capturing unrelated screenshots", () => {
  const steps = [
    { name: "default" },
    { name: "hover-card" },
    { name: "selected" },
    { name: "selected-hover" },
    { name: "search-results" },
  ];
  assert.deepEqual(selectExecutionSteps(steps, ["selected-hover"]), [
    steps[0],
    steps[1],
    steps[2],
    steps[3],
  ]);
  assert.deepEqual(selectExecutionSteps(steps, []), steps);
});

test("collects bounded repair targets from failed analysis", () => {
  const targets = collectRepairTargets(
    {
      issues: [
        {
          viewport: "mobile",
          state: "default",
          status: "mismatch",
        },
        {
          viewport: "tabletPortrait",
          state: "hover-card",
          status: "dimension-mismatch",
        },
        {
          viewport: "desktop",
          state: "selected",
          status: "mismatch",
        },
      ],
    },
    2,
  );

  assert.deepEqual(targets.viewports, ["mobile", "tabletPortrait"]);
  assert.deepEqual(targets.states, ["default", "hover-card"]);
  assert.equal(targets.selectedIssueCount, 2);
  assert.equal(targets.issueCount, 3);
});
