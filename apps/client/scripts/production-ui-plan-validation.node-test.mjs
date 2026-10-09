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

test("current production smoke stage bindings are valid", async () => {
  const plan = JSON.parse(await fs.readFile(
    new URL("../../../docs/operations/production-ui-smoke-plan.json", import.meta.url), "utf8"));
  assert.deepEqual(validateProductionUiPlan(plan), []);
});
