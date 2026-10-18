/** Conservative future-worker navigation guard. No browser I/O.
 *
 * This is NOT called by the live paired CAM agent or the browser pilot.
 * The backend's preview is informational and MUST NEVER be treated as
 * authority to execute, even if this pure predicate returns true.
 */
const ALLOWED_HOSTS = {
  etsy: new Set(["www.etsy.com"]),
  amazon: new Set([
    "www.amazon.com","amazon.com","www.amazon.co.uk","amazon.co.uk",
    "www.amazon.ca","amazon.ca","www.amazon.de","amazon.de",
    "www.amazon.fr","amazon.fr","www.amazon.es","amazon.es",
    "www.amazon.it","amazon.it","www.amazon.co.jp","amazon.co.jp",
  ]),
};
export const FUTURE_SCAN_LIMITS = Object.freeze({
  maxPages: 5, maxGalleryItems: 20, maxNavigationSeconds: 20,
  maxRedirects: 0, maxExtraPathComponents: 2,
  requestAllowed: false,
});

const PATH = /^\/[A-Za-z0-9_.~/-]+$/;

function parseShopUrl(input) {
  if (typeof input !== "string" || input.length < 12 || input.length > 2048 ||
      /[^\x21-\x7e]|[%\\@;?#]/.test(input)) return null;
  let url;
  try { url = new URL(input); } catch { return null; }
  // URL parsing normalizes /../, implicit :443, case and some backslashes.
  // Any rewrite could accidentally widen the server-provided scope.
  if (url.href !== input || url.protocol !== "https:" || url.search ||
      url.hash || url.username || url.password || url.port ||
      url.host !== url.hostname || !PATH.test(url.pathname)) return null;
  const segments = url.pathname.replace(/^\/+|\/+$/g, "").split("/");
  if (segments.length < 2 || segments.length > 12 ||
      segments.some(s => !s || s === "." || s === ".." ||
                       s.length > 120 || s.includes(".."))) return null;
  return {host: url.hostname, segments};
}

export function inspectCandidateUrlScope(platform, registeredUrl, candidateUrl) {
  const registered = parseShopUrl(registeredUrl);
  const candidate = parseShopUrl(candidateUrl);
  if (!registered || !ALLOWED_HOSTS[platform]?.has(registered.host)) {
    return {withinScope:false,reason:"registered_storefront_not_supported"};
  }
  if (platform === "etsy" &&
      (registered.segments.length !== 2 ||
       registered.segments[0].toLowerCase() !== "shop")) {
    return {withinScope:false,reason:"registered_storefront_not_supported"};
  }
  if (platform === "amazon" &&
      registered.segments[0].toLowerCase() !== "stores") {
    return {withinScope:false,reason:"registered_storefront_not_supported"};
  }
  if (!candidate) return {withinScope:false,reason:"candidate_url_invalid"};
  if (candidate.host !== registered.host) {
    return {withinScope:false,reason:"candidate_host_outside_shop"};
  }
  if (candidate.segments.length < registered.segments.length ||
      registered.segments.some((s,i) => s !== candidate.segments[i])) {
    return {withinScope:false,reason:"candidate_path_outside_shop"};
  }
  if (candidate.segments.length - registered.segments.length >
      FUTURE_SCAN_LIMITS.maxExtraPathComponents) {
    return {withinScope:false,reason:"candidate_path_depth_exceeded"};
  }
  return {withinScope:true,reason:"registered_storefront_subpath"};
}
