/**
 * Offline, credential-free validation of a Playwright production QA storage state.
 * The caller reads the state locally; this module never logs cookie values,
 * localStorage values, or full identity information.
 */
export function inspectProductionQaStorageState(state, productionUrl, nowSeconds = Date.now()/1000) {
  const url = new URL(productionUrl);
  const host = url.hostname.toLowerCase();
  const origin = url.origin;
  if (!state || !Array.isArray(state.cookies) || !Array.isArray(state.origins)) {
    return {ready:false,reason:"invalid-playwright-storage-state"};
  }
  const domainMatches = domain => {
    const clean = String(domain || "").replace(/^\./,"").toLowerCase();
    return clean.length > 0 && (host === clean || host.endsWith("." + clean));
  };
  const siteCookies = state.cookies.filter(c => domainMatches(c?.domain)
    && typeof c?.name === "string" && typeof c?.value === "string");
  const freshCookies = siteCookies.filter(c => {
    const expires = Number(c?.expires);
    return !Number.isFinite(expires) || expires < 0 || expires > nowSeconds + 60;
  });
  const siteOrigins = state.origins.filter(o => o?.origin === origin);
  const hasSiteLocalStorage = siteOrigins.some(o => Array.isArray(o.localStorage)
    && o.localStorage.some(item => item && typeof item.name === "string"
      && typeof item.value === "string"));
  if (freshCookies.length === 0 && !hasSiteLocalStorage) {
    return {ready:false,reason:siteCookies.length ? "production-auth-cookies-expired"
      : "no-production-origin-session"};
  }
  return {ready:true,reason:"state-has-production-origin-credentials",
    siteCookies:freshCookies.length,siteOrigins:siteOrigins.length};
}
