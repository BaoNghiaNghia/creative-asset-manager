import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { loadUiQaFixture } from "./ui-qa-fixture.mjs";

function getRoute(fixture, path) {
  const route = fixture.apiRoutes.find(item => item.method === "GET" && item.path === path);
  assert.ok(route, `Missing GET fixture response for ${path}`);
  assert.ok(!route.status || route.status === 200, `Fixture GET failed for ${path}`);
  return route.body;
}

test("Branding DNA browser QA covers active follow-on requests without bypassing scanner safety", async () => {
  const fixture = await loadUiQaFixture(
    fileURLToPath(new URL("./fixtures/branding-dna.json", import.meta.url)));
  const root = "/api/v1/branding-dna";
  const shop = `${root}/shops/qa-shop-1`;
  for (const url of [
    `${root}/status`, `${root}/shops`, `${root}/workers`,
    `${root}/scout-agents/presence`,
    `${shop}/scan-policy-reviews`, `${shop}/scan-readiness`,
    `${shop}/scan-tasks`, `${shop}/listings`,
    `${shop}/browser-plan`, `${shop}/handoffs`,
    `${shop}/knowledge`, `${shop}/dna-inputs`,
  ]) getRoute(fixture, url);
  const presence = getRoute(fixture, `${root}/scout-agents/presence`);
  const readiness = getRoute(fixture, `${shop}/scan-readiness`);
  const plan = getRoute(fixture, `${shop}/browser-plan`);
  assert.equal(presence.scan_enabled, false);
  assert.equal(presence.dispatch_state, "disabled");
  assert.equal(readiness.scan_enabled, false);
  assert.equal(readiness.dispatchable, false);
  assert.equal(readiness.marketplace_authorization_verified, false);
  assert.equal(readiness.policy_review.dispatchable, false);
  assert.equal(readiness.active_scan_jobs, 0);
  assert.equal(plan.scanner_enabled, false);
  assert.equal(plan.dispatchable, false);
  assert.equal(plan.marketplace_api_used, false);
  assert.deepEqual(getRoute(fixture, `${shop}/scan-tasks`).items, []);
});

test("AI Operations inventory tab has its active replay queue fixture", async () => {
  const fixture = await loadUiQaFixture(
    fileURLToPath(new URL("./fixtures/ai-operations.json", import.meta.url)));
  const jobs = getRoute(fixture, "/api/inventory/daily-sheet/gemini/replay/jobs");
  assert.deepEqual(jobs, { items: [] });
  assert.equal(getRoute(fixture, "/api/inventory/daily-sheet/status").execution_mode, "v4_slots");
});
