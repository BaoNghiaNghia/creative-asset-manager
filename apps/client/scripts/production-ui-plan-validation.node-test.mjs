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

test("current production smoke stage bindings are valid", async () => {
  const plan = JSON.parse(await fs.readFile(
    new URL("../../../docs/operations/production-ui-smoke-plan.json", import.meta.url), "utf8"));
  assert.deepEqual(validateProductionUiPlan(plan), []);
  const ai = plan.routes.find((route) => route.name === "ai-operations");
  assert.ok(ai?.requiresAuth, "authenticated AI Operations route must be present");
  assert.equal(ai.path, "/ai-operations");
  for (const tab of ["pipeline", "visual-search", "creative-pipeline", "inventory"]) {
    assert.ok(ai.states.some((state) => state.click === `[data-ops-tab='${tab}']`),
      `production smoke must exercise AI Operations ${tab} tab`);
  }
  assert.ok(ai.states[0].assertions.some((assertion) => assertion.type === "no-viewport-overflow"),
    "AI Operations production smoke must check responsive viewport overflow");
  const ugc = plan.routes.find((route) => route.name === "realistic-review-ugc");
  assert.ok(ugc, "authenticated UGC route must be present");
  for (let stage = 0; stage <= 5; stage += 1) {
    assert.ok(ugc.states.some((state) => state.click === `#rrugc-tab-stage${stage}`),
      `authenticated QA must exercise Stage ${stage}`);
  }
});
