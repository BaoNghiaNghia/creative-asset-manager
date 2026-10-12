import assert from "node:assert/strict";
import fs from "node:fs/promises";
import test from "node:test";
import { validateProductionUiPlan } from "./production-ui-plan-validation.mjs";

test("production plan catches cross-stage panel reference", () => {
  const broken = { routes: [{ name: "UGC", states: [{
    name: "stage5", click: "#rrugc-tab-stage5",
    assertions: [{ type: "width-ratio", selector: ".rrugc-stage3",
      relativeTo: "#rrugc-panel-stage3" }],
  }] }] };
  assert.match(validateProductionUiPlan(broken).join(" "), /tab Stage 5 targets panel Stage 3/);
});

test("production plan rejects UGC tabs from the wrong workflow group", () => {
  const broken = { routes: [{ name: "realistic-review-ugc", path: "/realistic-review-ugc",
    states: [{ name: "wrong", click: "#rrugc-tab-stage5" }] }] };
  assert.match(validateProductionUiPlan(broken).join(" "), /stage5 is not visible in products workflow/);
  const wrongReviews = { routes: [{ name: "realistic-review-ugc-reviews",
    path: "/realistic-review-ugc?workflow=reviews&stage=reviews-stage2",
    states: [{ name: "wrong", click: "#rrugc-tab-stage1" }] }] };
  assert.match(validateProductionUiPlan(wrongReviews).join(" "), /stage1 is not visible in reviews workflow/);
});

test("production plan rejects unsupported assertion types before browser execution", () => {
  const broken = { routes: [{ name: "AI Operations", states: [{
    name: "default",
    assertions: [{ type: "no-imaginary-overflow", selector: "body" }],
  }] }] };
  assert.match(
    validateProductionUiPlan(broken).join(" "),
    /unsupported Production UI assertion no-imaginary-overflow/,
  );
});

test("production Browser QA separates Products and Reviews stage navigation", async () => {
  const plan = JSON.parse(await fs.readFile(
    new URL("../../../docs/operations/production-ui-smoke-plan.json", import.meta.url), "utf8"));
  const products = plan.routes.find(route => route.name === "realistic-review-ugc");
  const reviews = plan.routes.find(route => route.name === "realistic-review-ugc-reviews");
  assert.ok(products);
  assert.ok(reviews, "Reviews requires its own authenticated deep-linked route");
  assert.equal(reviews.path, "/realistic-review-ugc?workflow=reviews&stage=reviews-stage2");
  for (const name of ["stage0", "stage1", "stage2"]) {
    assert.ok(products.states.some(state => state.click === "#rrugc-tab-" + name), name);
  }
  for (const name of ["reviews-stage2", "stage3", "stage4", "stage5"]) {
    assert.ok(reviews.states.some(state => state.click === "#rrugc-tab-" + name), name);
    assert.equal(products.states.some(state => state.click === "#rrugc-tab-" + name), false);
  }
});

test("current production smoke stage bindings are valid", async () => {
  const plan = JSON.parse(await fs.readFile(
    new URL("../../../docs/operations/production-ui-smoke-plan.json", import.meta.url), "utf8"));
  assert.deepEqual(validateProductionUiPlan(plan), []);
  const ai = plan.routes.find((route) => route.name === "ai-operations");
  assert.ok(ai?.requiresAuth, "authenticated AI Operations route must be present");
  assert.equal(ai.path, "/ai-operations");
  for (const tab of ["pipeline", "visual-search", "creative-pipeline"]) {
    assert.ok(ai.states.some((state) => state.click === `[data-ops-tab='${tab}']`),
      `production smoke must exercise AI Operations ${tab} tab`);
  }
  assert.ok(!ai.states.some((state) => state.click === "[data-ops-tab='inventory']"),
    "Inventory must not remain in AI Operations");
  const inventory = plan.routes.find((route) => route.name === "inventory");
  assert.ok(inventory?.requiresAuth, "Inventory must be checked as an authenticated workspace");
  assert.equal(inventory.path, "/inventory");
  assert.equal(inventory.ready, "main.inventory-workspace");
  assert.ok(ai.states[0].assertions.some((assertion) => assertion.type === "no-viewport-overflow"),
    "AI Operations production smoke must check responsive viewport overflow");
  const ugc = plan.routes.find((route) => route.name === "realistic-review-ugc");
  assert.ok(ugc, "authenticated UGC route must be present");
  for (let stage = 0; stage <= 2; stage += 1) {
    assert.ok(ugc.states.some((state) => state.click === `#rrugc-tab-stage${stage}`),
      `authenticated Products QA must exercise Stage ${stage}`);
  }
  const reviews = plan.routes.find((route) => route.name === "realistic-review-ugc-reviews");
  assert.ok(reviews?.requiresAuth, "authenticated Reviews route must be present");
  assert.ok(reviews.states.some((state) => state.click === "#rrugc-tab-reviews-stage2"));
  for (let stage = 3; stage <= 5; stage += 1) {
    assert.ok(reviews.states.some((state) => state.click === `#rrugc-tab-stage${stage}`),
      `authenticated Reviews QA must exercise Stage ${stage}`);
  }
});
