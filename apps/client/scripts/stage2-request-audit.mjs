/** Temporary read-only network probe for Stage 2. No credentials leave this process. */
import { firefox } from "playwright";
const base = "https://creative-assets.ddns.net";
const state = process.env.CAM_PRODUCTION_UI_STORAGE_STATE ||
  ["/etc", "creative-asset-manager", "production-ui-storage-state.json"].join("/");
const browser = await firefox.launch({ headless: true });
let inFlight = 0, peak = 0, blockedWrites = 0;
const pending = new Set();
const paths = new Map();
const failures = [];
try {
  const context = await browser.newContext({ storageState: state, viewport: { width: 1440, height: 900 }, serviceWorkers: "block" });
  await context.route("**/*", route => {
    const method = route.request().method().toUpperCase();
    if (!["GET", "HEAD", "OPTIONS"].includes(method)) { blockedWrites++; return route.abort(); }
    return route.continue();
  });
  const page = await context.newPage();
  const done = req => {
    if (pending.delete(req)) inFlight--;
  };
  page.on("request", req => {
    if (!req.url().startsWith(base)) return;
    const url = new URL(req.url());
    const key = req.method() + " " + url.pathname;
    pending.add(req);
    inFlight++;
    peak = Math.max(peak, inFlight);
    paths.set(key, (paths.get(key) || 0) + 1);
  });
  page.on("requestfinished", done);
  page.on("requestfailed", req => { done(req); if (req.url().startsWith(base)) failures.push(req.url().split("?")[0]); });
  await page.goto(base + "/realistic-review-ugc?workflow=products&stage=stage2", { waitUntil: "domcontentloaded", timeout: 45000 });
  await page.waitForTimeout(9000);
  const rowCount = await page.locator(".rrugc-products-photo-pipeline").count();
  const data = {
    rowCount, pageTitle: await page.title(), activeUrl: page.url(),
    total: Array.from(paths.values()).reduce((a,b)=>a+b,0), peak, blockedWrites,
    paths: [...paths].sort((a,b)=>b[1]-a[1]).slice(0,25),
    failures: failures.slice(0,8),
    stage2Visible: await page.locator("#rrugc-panel-stage2").isVisible(),
  };
  console.log("STAGE2_AUDIT", JSON.stringify(data));
  await context.close();
} finally { await browser.close(); }
