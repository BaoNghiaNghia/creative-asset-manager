import assert from "node:assert/strict";
import fs from "node:fs/promises";
import test from "node:test";

test("fixture serves Stage 4 completions feed used by GatorHats UI", async () => {
  const fixture = JSON.parse(await fs.readFile(
    new URL("./fixtures/realistic-review-ugc.json", import.meta.url), "utf8"));
  const feed = fixture.apiRoutes.find(route =>
    route.method === "GET" && route.path === "/api/v1/realistic-review-ugc/stage4/completions");
  assert.ok(feed, "missing feed fixture makes QA network fail with HTTP 599");
  assert.equal(feed.body.bootstrap, true);
  assert.ok(Array.isArray(feed.body.items));
  assert.equal(typeof feed.body.cursor, "string");
});

test("GatorHats QA targets the current Stage 5 toolbar and Reviews Stage 2", async () => {
  const plan = JSON.parse(await fs.readFile(
    new URL("../../../docs/operations/ui-qa-realistic-review-ugc-plan.json", import.meta.url), "utf8"));
  const stage5 = plan.steps.find(step => step.name === "stage5-actions");
  assert.ok(stage5);
  assert.ok(stage5.requireIcons.includes("#rrugc-panel-stage5 .rrugc-stage5-toolbar-actions > button"));
  assert.ok(!stage5.requireIcons.some(selector =>
    selector.includes("rrugc-stage3-header-actions") || selector.includes("rrugc-stage4-search-toolbar")));
  assert.ok(plan.steps.some(step => step.name === "reviews-stage2-actions" &&
    step.waitFor === "#rrugc-panel-reviews-stage2 .rrugc-colorway-stage"));
});
