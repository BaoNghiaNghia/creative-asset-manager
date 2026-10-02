import { chromium } from "playwright";
import fs from "node:fs/promises";
import path from "node:path";
import { installUiQaFixture, loadUiQaFixture } from "./ui-qa-fixture.mjs";

const VIEWPORTS = {
  desktop: { width: 1440, height: 900 },
  compact: { width: 1280, height: 800 },
  tabletLandscape: { width: 1024, height: 768 },
  tabletPortrait: { width: 768, height: 1024 },
  mobile: { width: 390, height: 844 },
};

function argValue(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : undefined;
}

function hasFlag(name) {
  return process.argv.includes(name);
}

function sanitize(value) {
  return value.replace(/[^a-zA-Z0-9._-]+/g, "-").replace(/^-+|-+$/g, "") || "state";
}

function isLoopback(hostname) {
  return hostname === "127.0.0.1" || hostname === "localhost" || hostname === "::1";
}

function assertAllowedUrl(rawUrl) {
  const url = new URL(rawUrl);
  const extraHosts = (process.env.CAM_UI_QA_ALLOWED_HOSTS || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);

  if (
    !isLoopback(url.hostname) &&
    !extraHosts.includes(url.hostname) &&
    process.env.CAM_UI_QA_ALLOW_REMOTE !== "1"
  ) {
    throw new Error(
      `Refusing remote UI QA host "${url.hostname}". Use loopback/staging, set CAM_UI_QA_ALLOWED_HOSTS, or explicitly set CAM_UI_QA_ALLOW_REMOTE=1.`,
    );
  }

  return url.toString();
}

async function readPlan(planPath) {
  if (!planPath) return { steps: [{ name: "default" }] };
  const raw = await fs.readFile(planPath, "utf8");
  const plan = JSON.parse(raw);
  if (!Array.isArray(plan.steps) || plan.steps.length === 0) {
    throw new Error("UI QA plan must contain a non-empty steps array.");
  }
  return plan;
}

async function cleanupRuns(outputRoot, keep) {
  await fs.mkdir(outputRoot, { recursive: true });
  const entries = await fs.readdir(outputRoot, { withFileTypes: true });
  const dirs = [];
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const fullPath = path.join(outputRoot, entry.name);
    const stat = await fs.stat(fullPath);
    dirs.push({ fullPath, mtimeMs: stat.mtimeMs });
  }
  dirs.sort((a, b) => b.mtimeMs - a.mtimeMs);
  await Promise.all(dirs.slice(keep).map((entry) => fs.rm(entry.fullPath, { recursive: true, force: true })));
}

async function performStep(page, step) {
  if (step.resetBefore) {
    await page.reload({ waitUntil: "domcontentloaded" });
  }
  if (step.click) {
    await page.locator(step.click).first().click();
  }
  if (step.hover) {
    await page.locator(step.hover).first().hover();
  }
  if (step.focus) {
    await page.locator(step.focus).first().focus();
  }
  if (step.fill?.selector) {
    await page.locator(step.fill.selector).first().fill(String(step.fill.value ?? ""));
  }
  if (step.press?.selector && step.press?.key) {
    await page.locator(step.press.selector).first().press(step.press.key);
  }
  if (step.waitFor) {
    await page.locator(step.waitFor).first().waitFor({ state: "visible" });
  }
  if (step.waitMs) {
    await page.waitForTimeout(Number(step.waitMs));
  }
}

const rawUrl = argValue("--url") || process.env.CAM_UI_QA_URL;
if (!rawUrl) {
  throw new Error("Missing --url or CAM_UI_QA_URL.");
}

const url = assertAllowedUrl(rawUrl);
const plan = await readPlan(argValue("--plan"));
const fixturePath = argValue("--fixture") || process.env.CAM_UI_QA_FIXTURE;
const fixture = await loadUiQaFixture(fixturePath);
const viewportNames = (
  argValue("--viewports") ||
  process.env.CAM_UI_VIEWPORTS ||
  (Array.isArray(plan.viewports) ? plan.viewports.join(",") : "desktop")
)
  .split(",")
  .map((item) => item.trim())
  .filter(Boolean);

for (const name of viewportNames) {
  if (!VIEWPORTS[name]) {
    throw new Error(`Unknown viewport "${name}". Available: ${Object.keys(VIEWPORTS).join(", ")}`);
  }
}

const outputRoot = path.resolve(argValue("--output") || process.env.CAM_UI_QA_OUTPUT || ".ui-qa");
const keepRuns = Math.max(1, Number(process.env.CAM_UI_QA_KEEP || 5));
await cleanupRuns(outputRoot, keepRuns);

const runId = new Date().toISOString().replace(/[:.]/g, "-");
const runDir = path.join(outputRoot, runId);
await fs.mkdir(runDir, { recursive: true });

const launchArgs = typeof process.getuid === "function" && process.getuid() === 0
  ? ["--no-sandbox", "--disable-setuid-sandbox"]
  : [];

const browser = await chromium.launch({
  channel: "chrome",
  headless: true,
  chromiumSandbox: false,
  args: launchArgs,
});

const report = {
  url,
  runId,
  strict: hasFlag("--strict"),
  fixture: fixture ? path.relative(process.cwd(), fixture.absolutePath) : null,
  results: [],
};

try {
  for (const viewportName of viewportNames) {
    const viewport = VIEWPORTS[viewportName];
    const context = await browser.newContext({ viewport });
    await installUiQaFixture(context, fixture, url);
    const page = await context.newPage();

    const issues = {
      consoleErrors: [],
      pageErrors: [],
      requestFailures: [],
      badResponses: [],
    };

    page.on("console", (message) => {
      if (message.type() === "error") issues.consoleErrors.push(message.text());
    });
    page.on("pageerror", (error) => issues.pageErrors.push(String(error)));
    page.on("requestfailed", (request) => {
      issues.requestFailures.push({
        method: request.method(),
        url: request.url(),
        error: request.failure()?.errorText || "request_failed",
      });
    });
    page.on("response", (response) => {
      if (response.status() >= 400) {
        issues.badResponses.push({ status: response.status(), url: response.url() });
      }
    });

    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30_000 });
    await page.waitForTimeout(Number(process.env.CAM_UI_QA_SETTLE_MS || 800));

    const screenshots = [];
    for (const step of plan.steps) {
      await performStep(page, step);
      const name = sanitize(step.name || "state");
      const filename = `${viewportName}--${name}.png`;
      const screenshotPath = path.join(runDir, filename);
      await page.screenshot({ path: screenshotPath, fullPage: step.fullPage !== false });
      screenshots.push(filename);
    }

    report.results.push({
      viewport: viewportName,
      size: viewport,
      screenshots,
      issues,
    });

    await context.close();
  }
} finally {
  await browser.close();
}

const reportPath = path.join(runDir, "report.json");
await fs.writeFile(reportPath, JSON.stringify(report, null, 2) + "\n", "utf8");

const issueCount = report.results.reduce(
  (total, result) =>
    total +
    result.issues.consoleErrors.length +
    result.issues.pageErrors.length +
    result.issues.requestFailures.length +
    result.issues.badResponses.length,
  0,
);

console.log(`UI QA complete: ${runDir}`);
console.log(`Viewports: ${viewportNames.join(", ")}`);
console.log(`Recorded issues: ${issueCount}`);

if (hasFlag("--strict") && issueCount > 0) {
  process.exitCode = 2;
}
