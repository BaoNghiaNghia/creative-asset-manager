/** Distinguish user-navigation cancellation from network and API failures. */

/**
 * Only a GET request that was already in flight BEFORE an explicit QA page
 * navigation may be classified as a navigation cancellation.
 * This is deliberately narrower than an endpoint allowlist: new requests,
 * aborted mutations, HTTP errors and connection failures remain failures.
 * Enable only for isolated fixture QA, never authenticated production QA.
 */
export function isFixtureNavigationCancellation(
  failure, appUrl, { fixture = false, startedAt, navigatedAt, failedAt } = {},
) {
  if (!fixture || failure?.error !== "net::ERR_ABORTED" ||
      failure?.method !== "GET" || !appUrl ||
      ![startedAt, navigatedAt, failedAt].every(Number.isFinite) ||
      startedAt > navigatedAt || failedAt < navigatedAt ||
      failedAt - navigatedAt > 5000) return false;
  try {
    const request = new URL(failure.url);
    const app = new URL(appUrl);
    return request.origin === app.origin;
  } catch {
    return false;
  }
}

export function isExpectedQaRequestAbort(failure, qaMode, appUrl) {
  if (failure?.error !== "net::ERR_ABORTED") return false;
  // Even repair and baseline flows must report aborted writes, other origins
  // and unexpected API reads. Their reports are not exempt from QA safety.
  if (failure.method !== "GET" || !appUrl) return false;
  try {
    const request = new URL(failure.url);
    const app = new URL(appUrl);
    return request.origin === app.origin
      && ((qaMode === "inventory-incident" && request.pathname === "/api/v1/auth/identity")
        || (request.pathname.startsWith("/assets/") && /\.woff2?$/.test(request.pathname))
        || request.pathname === "/api/v1/realistic-review-ugc/stage4/completions"
        || request.pathname === "/api/v1/realistic-review-ugc/keyword-analysis/search-intelligence"
        || (request.pathname === "/api/v1/realistic-review-ugc/source-plans"
          && request.searchParams.has("sort_by") && request.searchParams.has("sort_dir")));
  } catch {
    return false;
  }
}
