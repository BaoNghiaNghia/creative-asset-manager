import { chromium } from "../../client/node_modules/playwright/index.mjs";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../client/.ui-qa/scout-manager-desktop");
await mkdir(root, { recursive: true });
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_PATH ?? "/opt/google/chrome/chrome", args: ["--no-sandbox"] });
const url = process.env.SCOUT_MANAGER_PREVIEW_URL ?? "http://127.0.0.1:1420/";
const issues = [];
try {
  for (const [name,width,height] of [["desktop",1062,747],["compact",864,612]]) {
    const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
    page.on("pageerror", e => issues.push(name + ": " + e.message));
    await page.goto(url, { waitUntil: "networkidle", timeout: 30000 });
    if (await page.locator("h1").innerText() !== "RRUGC Scout Manager") throw Error("Title missing");
    if (await page.locator(".scout-card").count() !== 2) throw Error("Wrong Scout card count");
    if (await page.locator(".summary-item").count() !== 4) throw Error("Summary incomplete");
    if (!(await page.getByRole("button", { name: "Run automation" }).isDisabled())) {
      throw Error("Browser preview must disable privileged native controls");
    }
    const widths = await page.evaluate(() => [document.documentElement.scrollWidth, innerWidth]);
    if (widths[0] > widths[1] + 2) throw Error("Horizontal overflow at " + name + " " + widths);
    await page.screenshot({ path: resolve(root, name + ".png"), fullPage: true });
    console.log(name + ": screenshot and safety assertions passed");
    await page.close();
  }
  if (issues.length) throw Error("Browser errors: " + issues.join("; "));
  console.log("Scout Manager UI smoke: PASS (local preview only)");
} finally { await browser.close(); }
