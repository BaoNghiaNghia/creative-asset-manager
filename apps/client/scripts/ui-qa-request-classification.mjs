/** Distinguish user-navigation cancellation from network and API failures. */
export function isExpectedQaRequestAbort(failure, qaMode, appUrl) {
  if (failure?.error !== "net::ERR_ABORTED") return false;
  if (["repair", "baseline-proposal", "baseline-accept"].includes(qaMode)) return true;
  if (failure.method !== "GET" || !appUrl) return false;
  try {
    const request = new URL(failure.url);
    const app = new URL(appUrl);
    return request.origin === app.origin
      && (request.pathname === "/api/v1/realistic-review-ugc/stage4/completions"
        || (request.pathname === "/api/v1/realistic-review-ugc/source-plans"
          && request.searchParams.has("sort_by") && request.searchParams.has("sort_dir")));
  } catch {
    return false;
  }
}
