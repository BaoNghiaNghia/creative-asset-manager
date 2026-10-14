import assert from "node:assert/strict";
import fs from "node:fs/promises";
import test from "node:test";
import {
  assertFixturePermissions,
  checkFixturePermissions,
  requiredQaPermission,
} from "./ui-qa-fixture-rbac.mjs";

test("fixture preflight blocks incorrect RBAC instead of waiting for hidden pages", () => {
  const identity = { permissions: ["assets.read", "ai_operations.read"] };
  assert.deepEqual(checkFixturePermissions("/inventory/materials", { identity }), {
    status: "blocked", pathname: "/inventory/materials",
    requiredPermission: "inventory.read", missingPermissions: ["inventory.read"],
    reason: "fixture-rbac-mismatch",
  });
  assert.throws(() => assertFixturePermissions("http://127.0.0.1/inventory/materials", { identity }),
    /missing inventory\.read/);
  assert.equal(checkFixturePermissions("/inventory/materials",
    { identity: { permissions: ["inventory.read"] } }).status, "pass");
  assert.equal(checkFixturePermissions("/privacy-policy", { identity }).status, "pass");
  assert.equal(requiredQaPermission("/job-queue"), "ai_operations.read");
});

test("preflight permission mapping is consistent with AppRoute policy", async () => {
  const appRoute = await fs.readFile(
    new URL("../app/AppRoute.tsx", import.meta.url), "utf8");
  const routeGate = appRoute.split("export function requiredPermissionForApplicationRoute")[1]
    ?.split("export function AppRoute")[0] || "";
  for (const [route, permission] of [
    ['"ai-operations"', "ai_operations.read"],
    ['"job-queue"', "ai_operations.read"],
    ['"inventory"', "inventory.read"],
    ['"realistic-review-ugc"', "realistic_review_ugc.read"],
    ['"access-management"', "tenant_members.read"],
  ]) {
    assert.ok(routeGate.includes(route), `Missing route mapping for ${route}`);
    assert.ok(routeGate.includes(`return "${permission}"`), `Missing mapping to ${permission}`);
  }
});

test("actual Inventory fixture fails preflight until authorized test identity is provisioned", async () => {
  const fixture = JSON.parse(await fs.readFile(
    new URL("./fixtures/inventory.json", import.meta.url), "utf8"));
  const result = checkFixturePermissions("/inventory/materials", fixture);
  if (!fixture.identity.permissions.includes("inventory.read")) {
    assert.equal(result.status, "blocked");
    assert.deepEqual(result.missingPermissions, ["inventory.read"]);
  } else {
    assert.equal(result.status, "pass");
  }
});
