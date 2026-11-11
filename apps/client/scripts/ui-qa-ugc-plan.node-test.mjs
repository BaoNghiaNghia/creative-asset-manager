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


test("Stage 1 fixture returns nine-output V3 capability and the plan audits current skill labels", async () => {
  const fixture = JSON.parse(await fs.readFile(
    new URL("./fixtures/realistic-review-ugc.json", import.meta.url), "utf8"));
  const generation = fixture.apiRoutes.find(route =>
    route.method === "GET"
      && route.path === "/api/v1/realistic-review-ugc/keyword-creative-briefs/v3/generation-status");
  assert.ok(generation, "unhandled Stage 1 capability route would return HTTP 599");
  assert.equal(generation.body.available, true);
  assert.equal(generation.body.output_count, 9);
  const plan = JSON.parse(await fs.readFile(
    new URL("../../../docs/operations/ui-qa-realistic-review-ugc-plan.json", import.meta.url), "utf8"));
  const step = plan.steps.find(item => item.name === "stage1-actions");
  assert.ok(step);
  assert.ok(step.assertions.some(item =>
    item.type === "text-includes" && item.selector === "#rrugc-panel-stage1 .rrugc-keyword-gen-skill-cell"
    && item.value === "Creative Brief V3"));
  const products = plan.steps.find(item => item.name === "stage2-actions");
  assert.ok(products?.assertions.some(item =>
    item.type === "count" && item.selector === "#rrugc-panel-stage2 .rrugc-colorway-kpis article"
    && item.value === 2));
});

test("RRUGC fixture covers photo readiness and displayed output image without 599", async () => {
  const fixture = JSON.parse(await fs.readFile(
    new URL("./fixtures/realistic-review-ugc.json", import.meta.url), "utf8"));
  const get = path => fixture.apiRoutes.find(route => route.method === "GET" && route.path === path);
  const readiness = get("/api/v1/realistic-review-ugc/products-stage2/photo-pipeline/readiness");
  assert.equal(readiness?.body.ready, true);
  assert.equal(typeof readiness?.body.workflow, "string");
  const image = get("/api/v1/realistic-review-ugc/colorways/qa-colorway-1/output");
  assert.equal(image?.contentType, "image/png");
  assert.ok(image?.imageBase64?.length > 20);
});

test("Browser QA targets current Codex Pool header and reference review modal", async () => {
  const plan = JSON.parse(await fs.readFile(
    new URL("../../../docs/operations/ui-qa-realistic-review-ugc-plan.json", import.meta.url), "utf8"));
  assert.equal(plan.steps.find(step => step.name === "stage4-codex-manager-open")?.click,
    ".rrugc-global-management-actions [data-codex-accounts-open]");
  assert.equal(plan.steps.find(step => step.name === "candidate-review-open")?.waitFor,
    ".rrugc-source-review-modal .rrugc-source-review-votes");
  const research = JSON.parse(await fs.readFile(
    new URL("../../../docs/operations/ui-qa-research-inbox-plan.json", import.meta.url), "utf8"));
  assert.equal(research.steps.find(step => step.name === "research-inbox-open")?.click,
    "#rrugc-panel-stage0 .rrugc-stage0-research-trigger");
  assert.equal(research.steps.find(step => step.name === "research-inbox-close")?.click,
    ".rrugc-research-modal-close");
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
