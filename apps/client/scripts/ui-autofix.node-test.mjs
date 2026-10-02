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

test("selects the Review Board fixture-backed profile", () => {
  const plan = classifyUiTask("Adjust Review Board toolbar spacing and hover", [
    "apps/client/app/review-board/ReviewBoardPage.tsx",
  ]);
  assert.equal(plan.scope, "review-board");
  assert.equal(plan.visualProfileSupported, true);
  assert.equal(plan.profile, "review-board");
  assert.ok(plan.states.includes("default"));
  assert.ok(plan.states.includes("issue-hover"));
});

test("selects the RRUGC fixture-backed profile", () => {
  const plan = classifyUiTask("Fix Realistic Review UGC candidate grid on mobile", [
    "apps/client/app/realistic-review-ugc/RealisticReviewUgcPage.tsx",
  ]);
  assert.equal(plan.scope, "realistic-review-ugc");
  assert.equal(plan.visualProfileSupported, true);
  assert.equal(plan.profile, "realistic-review-ugc");
  assert.ok(plan.states.includes("candidate-hover"));
});

test("selects the AI Operations fixture-backed profile", () => {
  const plan = classifyUiTask("Adjust AI Operations filters and focus", [
    "apps/client/app/ai-operations/AiOperationsPage.tsx",
  ]);
  assert.equal(plan.scope, "ai-operations");
  assert.equal(plan.visualProfileSupported, true);
  assert.equal(plan.profile, "ai-operations");
  assert.ok(plan.states.includes("filter-focus"));
});

test("selects the Inventory fixture-backed profile", () => {
  const plan = classifyUiTask("Adjust Inventory material grid hover and search", [
    "apps/client/app/inventory/InventoryApp.tsx",
  ]);
  assert.equal(plan.scope, "inventory");
  assert.equal(plan.visualProfileSupported, true);
  assert.equal(plan.profile, "inventory");
  assert.ok(plan.states.includes("material-hover"));
  assert.ok(plan.states.includes("material-search-focus"));
});

test("selects the Access Management fixture-backed profile", () => {
  const plan = classifyUiTask("Adjust Access Management member row hover and role layout", [
    "apps/client/app/access-management/AccessManagementPage.tsx",
  ]);
  assert.equal(plan.scope, "access-management");
  assert.equal(plan.visualProfileSupported, true);
  assert.equal(plan.profile, "access-management");
  assert.ok(plan.states.includes("member-row-hover"));
  assert.ok(plan.states.includes("roles-tab"));
});

test("selects the Video Generation fixture-backed profile", () => {
  const plan = classifyUiTask("Adjust Video Generation prompt focus and reference image card", [
    "apps/client/app/video-generation/VideoGenerationPage.tsx",
  ]);
  assert.equal(plan.scope, "video-generation");
  assert.equal(plan.visualProfileSupported, true);
  assert.equal(plan.profile, "video-generation");
  assert.ok(plan.states.includes("prompt-focus"));
  assert.ok(plan.states.includes("reference-selected"));
});

test("selects the Job Queue fixture-backed profile", () => {
  const plan = classifyUiTask("Adjust Job Queue row hover and result modal", [
    "apps/client/app/job-queue/JobQueuePage.tsx",
  ]);
  assert.equal(plan.scope, "job-queue");
  assert.equal(plan.visualProfileSupported, true);
  assert.equal(plan.profile, "job-queue");
  assert.ok(plan.states.includes("queue-row-hover"));
  assert.ok(plan.states.includes("queue-result-modal"));
});

test("selects the Public Review fixture-backed profile", () => {
  const plan = classifyUiTask("Adjust Public Review card hover and media viewer", [
    "apps/client/app/public-review/PublicReviewRoute.tsx",
  ]);
  assert.equal(plan.scope, "public-review");
  assert.equal(plan.visualProfileSupported, true);
  assert.equal(plan.profile, "public-review");
  assert.ok(plan.states.includes("public-card-hover"));
  assert.ok(plan.states.includes("public-media-open"));
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
