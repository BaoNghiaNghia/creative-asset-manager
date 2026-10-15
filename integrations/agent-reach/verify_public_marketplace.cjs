"use strict";
// Optional and bounded public listing title verification, never a crawler.
// Browser binaries and npm dependencies must be provisioned separately.
const { URL } = require("node:url");
const path = require("node:path");
const fs = require("node:fs");

(async () => {
  const input = JSON.parse(fs.readFileSync(0, "utf8"));
  const url = new URL(input.url);
  const host = url.hostname.toLowerCase();
  const valid = {
    "www.etsy.com": /^\/listing\/\d+\/?$/,
    "www.amazon.com": /^\/dp\/[a-z0-9]{10}\/?$/i,
    "www.pinterest.com": /^\/pin\/\d+\/?$/,
  };
  if (url.protocol !== "https:" || url.username || url.password ||
      url.port || !valid[host]?.test(url.pathname) || url.search || url.hash) {
    throw Error("unsupported_listing_url");
  }
  const clientPackage = path.resolve(__dirname, "../../apps/client");
  const playwright = require(require.resolve("playwright", {paths: [clientPackage]}));
  const browser = await playwright.firefox.launch({ headless: true, timeout: 6000 });
  try {
    const context = await browser.newContext({
      acceptDownloads: false, javaScriptEnabled: false, serviceWorkers: "block",
    });
    const page = await context.newPage();
    await page.route("**/*", route => {
      const request = route.request();
      try {
        const target = new URL(request.url());
        if (target.protocol === "https:" && target.hostname.toLowerCase() === host &&
            request.resourceType() === "document") return route.continue();
      } catch (_) { /* abort invalid URL */ }
      return route.abort();
    });
    const response = await page.goto(url.href, {
      waitUntil: "domcontentloaded", timeout: 8000,
    });
    if (!response || response.status() >= 400 ||
        new URL(page.url()).hostname.toLowerCase() !== host) {
      throw Error("page_not_public");
    }
    const title = (await page.locator("h1").first().textContent({timeout: 1500})
      .catch(() => "") || await page.title()).replace(/\s+/g," ").slice(0,200);
    if (!title || /captcha|robot|sign in|log in|access denied/i.test(title)) {
      throw Error("page_requires_review");
    }
    process.stdout.write(JSON.stringify({status:"ok", title}));
  } finally {
    await browser.close();
  }
})().catch(() => { process.stdout.write(JSON.stringify({status:"unavailable"})); process.exitCode = 2; });
