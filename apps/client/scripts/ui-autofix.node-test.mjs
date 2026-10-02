import assert from "node:assert/strict";
import test from "node:test";
import { classifyUiTask, buildAutofixSessionId } from "./ui-autofix-plan.mjs";
import { selectSmartTests } from "./ui-smart-tests.mjs";

test("classifies responsive Asset Explorer card work into bounded visual targets", () => {
  const plan = classifyUiTask("Fix card hover and selected state on mobile and iPad", [
    "apps/client/app/components/AssetGrid.tsx",
  ]);
  assert.equal(plan.scope, "asset-explorer");
  assert.equal(plan.visualProfileSupported, true);
  assert.deepEqual(plan.viewports, ["tabletPortrait", "mobile"]);
  assert.deepEqual(plan.states, ["hover-card", "selected", "selected-hover"]);
});

test("marks non-Explorer UI as requiring a future fixture profile", () => {
  const plan = classifyUiTask("Adjust Review Board toolbar spacing", [
    "apps/client/app/review-board/ReviewBoardPage.tsx",
  ]);
  assert.equal(plan.scope, "review-board");
  assert.equal(plan.visualProfileSupported, false);
  assert.equal(plan.profile, null);
});

test("autofix session id is stable for one task and base commit", () => {
  assert.equal(
    buildAutofixSessionId("same task", "abc"),
    buildAutofixSessionId("same task", "abc"),
  );
  assert.notEqual(
    buildAutofixSessionId("same task", "abc"),
    buildAutofixSessionId("different task", "abc"),
  );
});

test("smart test selector finds component-linked tests", async () => {
  const result = await selectSmartTests({
    changedFiles: ["apps/client/app/components/AssetGrid.tsx"],
    maxTests: 12,
  });
  assert.equal(result.mode, "targeted");
  assert.ok(result.tests.some((file) => file.endsWith("AssetGrid.test.ts")));
  assert.ok(result.tests.some((file) => file.endsWith("AssetGrid.share.test.tsx")));
  assert.ok(result.tests.length <= 4);
});

test("smart test selector skips automation-only files", async () => {
  const result = await selectSmartTests({
    changedFiles: ["apps/client/scripts/ui-qa.mjs"],
  });
  assert.equal(result.mode, "none");
});

test("smart test selector escalates security-sensitive UI", async () => {
  const result = await selectSmartTests({
    changedFiles: ["apps/client/app/public-review/PublicReviewRoute.tsx"],
  });
  assert.equal(result.mode, "full");
});
