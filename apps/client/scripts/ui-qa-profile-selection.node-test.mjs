import assert from "node:assert/strict";
import test from "node:test";
import { selectUiQaProfiles } from "./ui-qa-profile-selection.mjs";
import { UI_QA_PROFILES } from "./ui-qa-profiles.mjs";

test("cross-workspace foundation changes select every visual profile", () => {
  const result = selectUiQaProfiles(["apps/client/styles/ui-foundation.css"]);
  assert.equal(result.mode, "all");
  assert.deepEqual(result.profiles, Object.keys(UI_QA_PROFILES));
});

test("shared UI QA runtime changes select every visual profile", () => {
  const result = selectUiQaProfiles(["apps/client/scripts/ui-qa-profile-selection.mjs"]);
  assert.equal(result.mode, "all");
  assert.deepEqual(result.profiles, Object.keys(UI_QA_PROFILES));
});

test("AI Operations shared feature changes cover operations and queue", () => {
  const result = selectUiQaProfiles([
    "apps/client/features/ai_operations/api.ts",
  ]);
  assert.equal(result.mode, "selected");
  assert.deepEqual(result.profiles, ["ai-operations", "job-queue"]);
});

test("Inventory changes also cover the embedded AI Operations inventory view", () => {
  const result = selectUiQaProfiles([
    "apps/client/app/inventory/InventoryDailyPipeline.tsx",
  ]);
  assert.deepEqual(result.profiles, ["ai-operations", "inventory"]);
});

test("RichAnnotation changes cover Public Review and Review Board", () => {
  const result = selectUiQaProfiles([
    "apps/client/app/public-review/RichAnnotation.tsx",
  ]);
  assert.deepEqual(result.profiles, ["review-board", "public-review"]);
});

test("fixture and plan changes select their matching profile", () => {
  assert.deepEqual(
    selectUiQaProfiles(["apps/client/scripts/fixtures/video-generation.json"]).profiles,
    ["video-generation"],
  );
  assert.deepEqual(
    selectUiQaProfiles(["docs/operations/ui-qa-access-management-plan.json"]).profiles,
    ["access-management"],
  );
});

test("unmapped frontend work falls back to the explorer profile", () => {
  const result = selectUiQaProfiles(["apps/client/app/utils/textPreview.ts"]);
  assert.equal(result.mode, "default");
  assert.deepEqual(result.profiles, ["explorer-viewer"]);
});
