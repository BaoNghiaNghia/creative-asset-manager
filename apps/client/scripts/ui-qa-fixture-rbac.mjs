/**
 * Fixture RBAC preflight: fail fast when simulated identity cannot access the
 * route being tested. Never grant, inject, or elevate test permissions here.
 * This mirrors AppRoute.requiredPermissionForApplicationRoute; the companion
 * source-contract test catches drift between the two definitions.
 */
export const REQUIRED_QA_PERMISSIONS = Object.freeze({
  "/ai-operations": "ai_operations.read",
  "/job-queue": "ai_operations.read",
  "/branding-dna": "ai_operations.read",
  "/inventory": "inventory.read",
  "/realistic-review-ugc": "realistic_review_ugc.read",
  "/settings/access": "tenant_members.read",
});

export function requiredQaPermission(pathname) {
  const path = String(pathname || "/").replace(/\/$/, "") || "/";
  for (const [prefix, permission] of Object.entries(REQUIRED_QA_PERMISSIONS)) {
    if (path === prefix || path.startsWith(prefix + "/")) return permission;
  }
  return null;
}

export function checkFixturePermissions(url, fixture) {
  const pathname = new URL(url, "http://127.0.0.1").pathname;
  const requiredPermission = requiredQaPermission(pathname);
  const permissions = Array.isArray(fixture?.identity?.permissions)
    ? fixture.identity.permissions : [];
  if (!fixture || !requiredPermission) return {
    status: "pass", pathname, requiredPermission, missingPermissions: [],
  };
  const missingPermissions = permissions.includes(requiredPermission)
    ? [] : [requiredPermission];
  return {
    status: missingPermissions.length ? "blocked" : "pass",
    pathname,
    requiredPermission,
    missingPermissions,
    reason: missingPermissions.length
      ? "fixture-rbac-mismatch" : null,
  };
}

export function assertFixturePermissions(url, fixture) {
  const result = checkFixturePermissions(url, fixture);
  if (result.status !== "pass") {
    throw new Error(
      `UI QA fixture RBAC mismatch at ${result.pathname}: missing ${result.missingPermissions.join(", ")}. `
      + "Fixture authorization must match the real route gate; this QA cannot be reported as PASS.",
    );
  }
  return result;
}
