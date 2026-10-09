import { chromium, firefox, webkit } from "playwright";
import fs from "node:fs/promises";
import path from "node:path";
import pixelmatch from "pixelmatch";
import { PNG } from "pngjs";
import {
  buildChangedMask,
  clusterChangedRegions,
  createVisualAnalysis,
  enrichRegions,
  issueSearchTokens,
  paintRegionBoxes,
  renderVisualAnalysisMarkdown,
} from "./ui-qa-visual-analysis.mjs";
import { cleanupRuns } from "./ui-qa-cleanup.mjs";
import { assertUiStep } from "./ui-qa-assertions.mjs";
import { assertQualityBudgets } from "./ui-qa-quality.mjs";
import { installUiQaFixture, loadUiQaFixture } from "./ui-qa-fixture.mjs";
import {
  parseCsvList,
  selectExecutionSteps,
  selectPlanSteps,
} from "./ui-qa-targeting.mjs";

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

async function fileExists(filePath) {
  try {
    await fs.access(filePath);
    return true;
  } catch {
    return false;
  }
}

function boundedNumber(rawValue, fallback, minimum, maximum, name) {
  const value = rawValue === undefined || rawValue === "" ? fallback : Number(rawValue);
  if (!Number.isFinite(value) || value < minimum || value > maximum) {
    throw new Error(`${name} must be between ${minimum} and ${maximum}.`);
  }
  return value;
}

function extractStepActions(step) {
  const actions = [];
  for (const action of ["click", "hover", "focus"]) {
    if (step[action]) actions.push({ action, selector: step[action] });
  }
  if (step.fill?.selector) {
    actions.push({ action: "fill", selector: step.fill.selector });
  }
  if (step.press?.selector) {
    actions.push({ action: "press", selector: step.press.selector });
  }
  if (step.waitFor) {
    actions.push({ action: "waitFor", selector: step.waitFor });
  }
  return actions;
}

async function captureActionTargets(page, actions) {
  const scroll = await page.evaluate(() => ({
    x: window.scrollX,
    y: window.scrollY,
  }));
  const targets = [];
  for (const action of actions) {
    try {
      const box = await page.locator(action.selector).first().boundingBox();
      if (!box) continue;
      targets.push({
        ...action,
        rect: {
          x: Math.round(box.x + scroll.x),
          y: Math.round(box.y + scroll.y),
          width: Math.round(box.width),
          height: Math.round(box.height),
        },
      });
    } catch {
      // The action itself was already executed successfully; diagnostics are best-effort.
    }
  }
  return targets;
}

async function captureDomElements(page) {
  return page.evaluate(() => {
    const entries = [];
    const nodes = Array.from(document.body?.querySelectorAll("*") || []);
    const excluded = new Set(["script", "style", "meta", "link", "noscript"]);

    for (const element of nodes) {
      if (entries.length >= 1500) break;
      const tag = element.localName || "";
      if (!tag || excluded.has(tag)) continue;
      const rect = element.getBoundingClientRect();
      if (rect.width < 4 || rect.height < 4) continue;
      const style = window.getComputedStyle(element);
      if (
        style.display === "none" ||
        style.visibility === "hidden" ||
        Number(style.opacity) === 0
      ) {
        continue;
      }

      const id = element.id || null;
      const testId = element.getAttribute("data-testid");
      const ariaLabel = element.getAttribute("aria-label");
      const role = element.getAttribute("role");
      const classes = Array.from(element.classList || []).slice(0, 5);
      let selector = tag;
      if (id) {
        selector = `#${CSS.escape(id)}`;
      } else if (testId) {
        selector = `[data-testid=${JSON.stringify(testId)}]`;
      } else if (ariaLabel) {
        selector = `${tag}[aria-label=${JSON.stringify(ariaLabel)}]`;
      } else if (classes.length > 0) {
        selector = tag + classes.map((name) => `.${CSS.escape(name)}`).join("");
      } else if (role) {
        selector = `${tag}[role=${JSON.stringify(role)}]`;
      }

      entries.push({
        selector,
        tag,
        id,
        testId,
        ariaLabel,
        role,
        classes,
        rect: {
          x: Math.round(rect.left + window.scrollX),
          y: Math.round(rect.top + window.scrollY),
          width: Math.round(rect.width),
          height: Math.round(rect.height),
        },
      });
    }

    return entries;
  });
}

async function writeDiagnosticImage(screenshotPath, diagnosticPath, regions) {
  const actual = PNG.sync.read(await fs.readFile(screenshotPath));
  const annotated = new PNG({ width: actual.width, height: actual.height });
  annotated.data.set(actual.data);
  paintRegionBoxes(annotated.data, actual.width, actual.height, regions);
  await fs.mkdir(path.dirname(diagnosticPath), { recursive: true });
  await fs.writeFile(diagnosticPath, PNG.sync.write(annotated));
}

function changedFilesFromEnvironment() {
  return (process.env.CAM_UI_CHANGED_FILES || "")
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean);
}

async function rankSourceHints(issueLike) {
  const tokens = issueSearchTokens(issueLike);
  if (tokens.length === 0) return [];

  const repositoryRoot = path.resolve(process.cwd(), "../..");
  const eligible = changedFilesFromEnvironment().filter(
    (filePath) =>
      /^(apps\/client\/|packages\/)/.test(filePath) &&
      /\.(?:css|scss|ts|tsx|js|jsx|mjs)$/.test(filePath),
  );
  const hints = [];

  for (const filePath of eligible) {
    const absolutePath = path.resolve(repositoryRoot, filePath);
    if (!absolutePath.startsWith(repositoryRoot + path.sep)) continue;
    let source;
    try {
      source = await fs.readFile(absolutePath, "utf8");
    } catch {
      continue;
    }

    const matches = [];
    let score = 0;
    for (const token of tokens) {
      const occurrences = source.split(token).length - 1;
      if (occurrences <= 0) continue;
      matches.push(token);
      score += 1 + Math.min(5, occurrences);
    }
    if (score > 0) {
      hints.push({ path: filePath, score, matches: matches.slice(0, 8) });
    }
  }

  return hints
    .sort((a, b) => b.score - a.score || a.path.localeCompare(b.path))
    .slice(0, 5);
}

async function compareScreenshot({
  actualPath,
  baselinePath,
  diffPath,
  pixelThreshold,
  maxDiffRatio,
}) {
  if (!(await fileExists(baselinePath))) {
    return {
      status: "missing-baseline",
      mismatchedPixels: null,
      diffRatio: null,
      baseline: baselinePath,
      diff: null,
      regions: [],
    };
  }

  const actual = PNG.sync.read(await fs.readFile(actualPath));
  const baseline = PNG.sync.read(await fs.readFile(baselinePath));
  const actualSize = { width: actual.width, height: actual.height };
  const baselineSize = { width: baseline.width, height: baseline.height };
  if (actual.width !== baseline.width || actual.height !== baseline.height) {
    return {
      status: "dimension-mismatch",
      mismatchedPixels: null,
      diffRatio: 1,
      actualSize,
      baselineSize,
      baseline: baselinePath,
      diff: null,
      regions: [],
    };
  }

  const diff = new PNG({ width: actual.width, height: actual.height });
  const mismatchedPixels = pixelmatch(
    actual.data,
    baseline.data,
    diff.data,
    actual.width,
    actual.height,
    { threshold: pixelThreshold, includeAA: false },
  );
  const diffRatio = mismatchedPixels / (actual.width * actual.height);
  const status = diffRatio > maxDiffRatio ? "mismatch" : "match";
  let regions = [];

  if (status === "mismatch") {
    await fs.mkdir(path.dirname(diffPath), { recursive: true });
    await fs.writeFile(diffPath, PNG.sync.write(diff));

    const diffMask = new PNG({ width: actual.width, height: actual.height });
    pixelmatch(
      actual.data,
      baseline.data,
      diffMask.data,
      actual.width,
      actual.height,
      { threshold: pixelThreshold, includeAA: false, diffMask: true },
    );
    regions = clusterChangedRegions(
      buildChangedMask(diffMask),
      actual.width,
      actual.height,
      {
        tileSize: Math.round(
          boundedNumber(
            process.env.CAM_UI_VISUAL_REGION_TILE_SIZE,
            12,
            4,
            64,
            "CAM_UI_VISUAL_REGION_TILE_SIZE",
          ),
        ),
        joinRadiusTiles: Math.round(
          boundedNumber(
            process.env.CAM_UI_VISUAL_REGION_JOIN_RADIUS,
            2,
            1,
            4,
            "CAM_UI_VISUAL_REGION_JOIN_RADIUS",
          ),
        ),
        maxRegions: Math.round(
          boundedNumber(
            process.env.CAM_UI_VISUAL_MAX_REGIONS,
            8,
            1,
            20,
            "CAM_UI_VISUAL_MAX_REGIONS",
          ),
        ),
      },
    );
  }

  return {
    status,
    mismatchedPixels,
    diffRatio,
    actualSize,
    baselineSize,
    baseline: baselinePath,
    diff: status === "mismatch" ? diffPath : null,
    regions,
  };
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
  const settleMs = Number(
    step.settleMs ?? process.env.CAM_UI_QA_STEP_SETTLE_MS ?? 250,
  );
  if (Number.isFinite(settleMs) && settleMs > 0) {
    await page.waitForTimeout(settleMs);
  }
  await assertUiStep(page, step.assertions);
}

const rawUrl = argValue("--url") || process.env.CAM_UI_QA_URL;
if (!rawUrl) {
  throw new Error("Missing --url or CAM_UI_QA_URL.");
}

const url = assertAllowedUrl(rawUrl);
const plan = await readPlan(argValue("--plan"));
const requestedStates = parseCsvList(
  argValue("--states") || process.env.CAM_UI_STATES || "",
);
const captureSteps = selectPlanSteps(plan.steps, requestedStates);
const executionSteps = selectExecutionSteps(plan.steps, requestedStates);
const captureStateNames = new Set(
  captureSteps.map((step) => sanitize(step.name || "state")),
);
const qaMode = argValue("--mode") || process.env.CAM_UI_QA_MODE || "full";
const fixturePath = argValue("--fixture") || process.env.CAM_UI_QA_FIXTURE;
const fixture = await loadUiQaFixture(fixturePath);
const baselineArg = argValue("--baseline-dir") || process.env.CAM_UI_VISUAL_BASELINE_DIR;
const baselineDir = baselineArg ? path.resolve(baselineArg) : null;
const updateBaselines =
  hasFlag("--update-baselines") || process.env.CAM_UI_VISUAL_UPDATE === "1";
if (updateBaselines) {
  throw new Error(
    "Direct visual baseline writes are disabled. Create a governed proposal and explicitly accept it instead.",
  );
}
if (updateBaselines && requestedStates.length > 0) {
  throw new Error(
    "Targeted --states runs cannot update visual baselines. Run the full baseline update workflow instead.",
  );
}
const pixelThreshold = boundedNumber(
  process.env.CAM_UI_VISUAL_PIXEL_THRESHOLD,
  0.1,
  0,
  1,
  "CAM_UI_VISUAL_PIXEL_THRESHOLD",
);
const maxDiffRatio = boundedNumber(
  process.env.CAM_UI_VISUAL_MAX_DIFF_RATIO,
  0.001,
  0,
  1,
  "CAM_UI_VISUAL_MAX_DIFF_RATIO",
);
if (updateBaselines && !baselineDir) {
  throw new Error("--update-baselines requires --baseline-dir or CAM_UI_VISUAL_BASELINE_DIR.");
}
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

const browserChoice = process.env.CAM_UI_QA_BROWSER || "chrome";
if (!["chrome", "chromium", "firefox", "webkit"].includes(browserChoice)) {
  throw new Error(`Unknown Browser QA engine "${browserChoice}".`);
}
const browserEngine = browserChoice === "webkit" ? webkit : browserChoice === "firefox" ? firefox : chromium;
const browser = await browserEngine.launch(
  browserEngine !== chromium
    ? { headless: true }
    : {
        // CI uses the locked Chromium build; VPS keeps system Chrome.
        channel: browserChoice === "chromium" ? undefined : "chrome",
        headless: true,
        chromiumSandbox: false,
        args: launchArgs,
      },
);

const report = {
  url,
  runId,
  browser: { engine: browserChoice, version: browser.version() },
  strict: hasFlag("--strict"),
  mode: qaMode,
  states: captureSteps.map((step) => sanitize(step.name || "state")),
  fixture: fixture ? path.relative(process.cwd(), fixture.absolutePath) : null,
  visual: baselineDir
    ? {
        baselineDir: path.relative(process.cwd(), baselineDir),
        updateBaselines,
        pixelThreshold,
        maxDiffRatio,
      }
    : null,
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
      ignoredRequestFailures: [],
      badResponses: [],
      stepFailures: [],
    };

    page.on("console", (message) => {
      if (message.type() === "error") issues.consoleErrors.push(message.text());
    });
    page.on("pageerror", (error) => issues.pageErrors.push(String(error)));
    page.on("requestfailed", (request) => {
      const failure = {
        method: request.method(),
        url: request.url(),
        error: request.failure()?.errorText || "request_failed",
      };
      if (
        ["repair", "baseline-proposal", "baseline-accept"].includes(qaMode) &&
        failure.error === "net::ERR_ABORTED"
      ) {
        issues.ignoredRequestFailures.push(failure);
        return;
      }
      issues.requestFailures.push(failure);
    });
    page.on("response", (response) => {
      if (response.status() >= 400) {
        issues.badResponses.push({ status: response.status(), url: response.url() });
      }
    });

    const screenshots = [];
    const visualComparisons = [];
    const iconAudit = [];
    const qualityAudit = [];
    try {
      await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30_000 });
      await page.waitForTimeout(Number(process.env.CAM_UI_QA_SETTLE_MS || 800));

      for (const step of executionSteps) {
        try {
          await performStep(page, step);
      if (viewportName === "mobile") {
        await assertUiStep(page, plan.mobileAssertions || []);
        await assertUiStep(page, step.mobileAssertions || []);
      }
      // Optional design-system contract: critical action buttons must retain
      // a visible SVG glyph as well as their accessible text label.
      if (Array.isArray(step.requireIcons)) {
        await assertUiStep(page, step.requireIcons.map(selector => ({ type: "icon-visible", selector })));
      }
      const name = sanitize(step.name || "state");
      if (!captureStateNames.has(name)) continue;
      const filename = `${viewportName}--${name}.png`;
      const screenshotPath = path.join(runDir, filename);
      await page.screenshot({
        path: screenshotPath,
        fullPage: step.fullPage !== false,
        animations: "disabled",
        caret: "hide",
      });
      screenshots.push(filename);
      // Broad, non-blocking inventory: distinguish missing icon candidates
      // from buttons already using SVG, images, or CSS-based icon elements.
      const audit = await page.locator("button").evaluateAll(nodes => {
        const visible = nodes.filter(node => {
          const style = getComputedStyle(node);
          const box = node.getBoundingClientRect();
          return style.display !== "none" && style.visibility !== "hidden" &&
            box.width > 0 && box.height > 0;
        });
        const plain = [];
        const unlabeled = [];
        let withIcon = 0;
        for (const node of visible) {
          const name = (node.getAttribute("aria-label") ||
            node.getAttribute("title") || node.textContent || "").trim().replace(/\\s+/g, " ").slice(0, 90);
          const hasIcon = Boolean(node.querySelector("svg, img, i, [class*=icon], [class*=Icon]"));
          if (hasIcon) withIcon += 1;
          else if (plain.length < 40) plain.push({
            label: name, className: String(node.className || "").slice(0, 110),
          });
          if (!name && unlabeled.length < 20) {
            unlabeled.push(String(node.className || "").slice(0, 110));
          }
        }
        return { total: visible.length, withIcon, withoutIcon: visible.length - withIcon,
          plain, unlabeled };
      });
      iconAudit.push({ state: name, ...audit });
      // Read-only accessibility, mobile overflow, and performance signals.
      // These are inventory metrics until project-specific budgets are approved.
      const quality = await page.evaluate(() => {
        const navigation = performance.getEntriesByType("navigation")[0];
        const resources = performance.getEntriesByType("resource");
        const visible = element => {
          const style = getComputedStyle(element);
          const box = element.getBoundingClientRect();
          return style.display !== "none" && style.visibility !== "hidden" &&
            box.width > 0 && box.height > 0;
        };
        const unnamedControls = [...document.querySelectorAll("button, [role=button]")]
          .filter(visible).filter(element => !(element.getAttribute("aria-label") ||
            element.getAttribute("title") || element.textContent || "").trim()).length;
        const missingAlt = [...document.images].filter(visible)
          .filter(image => !image.hasAttribute("alt")).length;
        const tooSmall = [...document.querySelectorAll("button, [role=button]")]
          .filter(visible).filter(el => {
            const box = el.getBoundingClientRect();
            return box.width < 24 || box.height < 24;
          });
        const smallTargets = tooSmall.length;
        const smallTargetExamples = tooSmall.slice(0, 15).map(el => {
          const box = el.getBoundingClientRect();
          return { label: (el.getAttribute("aria-label") || el.textContent || "").trim().slice(0, 55),
            className: String(el.className || "").slice(0, 95),
            width: Math.round(box.width), height: Math.round(box.height) };
        });
        const viewportOverflowPx = Math.max(0, document.documentElement.scrollWidth - window.innerWidth);
        const overflowingElements = (viewportOverflowPx > 0 ? [...document.querySelectorAll("body *")] : [])
          .filter(visible).map(el => ({ el, box: el.getBoundingClientRect() }))
          .filter(({box}) => box.right > window.innerWidth + 1 && box.left < window.innerWidth)
          .slice(0, 18).map(({el, box}) => ({
            tag: el.tagName.toLowerCase(), id: el.id || null,
            className: typeof el.className === "string" ? el.className.slice(0, 100) : "",
            text: (el.textContent || "").trim().slice(0, 45),
            parent: typeof el.parentElement?.className === "string" ? el.parentElement.className.slice(0, 100) : "",
            ancestor: typeof el.parentElement?.parentElement?.className === "string" ? el.parentElement.parentElement.className.slice(0, 100) : "",
            right: Math.round(box.right), left: Math.round(box.left),
          }));
        const layoutRoots = ["html", "body", ".rrugc-shell", ".rrugc-main",
          ".rrugc-stage-tabs-shell", ".rrugc-stage-tabs"].map(selector => {
          const el = document.querySelector(selector);
          if (!el) return { selector, missing: true };
          const box = el.getBoundingClientRect();
          const css = getComputedStyle(el);
          return { selector, width: Math.round(box.width), right: Math.round(box.right),
            scrollWidth: el.scrollWidth, clientWidth: el.clientWidth, overflowX: css.overflowX };
        });
        return {
          viewportOverflowPx,
          layoutRoots, overflowingElements, unnamedControls, missingAlt, smallTargets, smallTargetExamples,
          domContentLoadedMs: Math.round(navigation?.domContentLoadedEventEnd || 0),
          resourceCount: resources.length,
          resourceTransferKb: Math.round(resources.reduce((sum, r) => sum + (r.transferSize || 0), 0) / 1024),
        };
      });
      qualityAudit.push({ state: name, ...quality });
      const viewportBudgets = plan.qualityBudgets?.[viewportName] || {};
      const stateBudgets = step.qualityBudgets?.[viewportName] || {};
      assertQualityBudgets(quality, { ...viewportBudgets, ...stateBudgets }, name);

      if (baselineDir) {
        const baselinePath = path.join(baselineDir, filename);
        const actions = extractStepActions(step);
        if (updateBaselines) {
          await fs.mkdir(baselineDir, { recursive: true });
          await fs.copyFile(screenshotPath, baselinePath);
          visualComparisons.push({
            state: name,
            screenshot: filename,
            status: "updated",
            mismatchedPixels: 0,
            diffRatio: 0,
            baseline: path.relative(process.cwd(), baselinePath),
            diff: null,
            diagnostic: null,
            actions,
            regions: [],
            sourceHints: [],
          });
        } else {
          const comparison = await compareScreenshot({
            actualPath: screenshotPath,
            baselinePath,
            diffPath: path.join(runDir, "diffs", filename),
            pixelThreshold,
            maxDiffRatio,
          });
          comparison.state = name;
          comparison.actions = actions;
          comparison.diagnostic = null;

          if (comparison.status === "mismatch" && comparison.regions.length > 0) {
            const [domElements, actionTargets] = await Promise.all([
              captureDomElements(page),
              captureActionTargets(page, actions),
            ]);
            comparison.regions = enrichRegions(
              comparison.regions,
              domElements,
              actionTargets,
              comparison.actualSize.width,
              comparison.actualSize.height,
            );
            const diagnosticPath = path.join(runDir, "diagnostics", filename);
            await writeDiagnosticImage(
              screenshotPath,
              diagnosticPath,
              comparison.regions,
            );
            comparison.diagnostic = diagnosticPath;
          }

          if (
            ["missing-baseline", "dimension-mismatch", "mismatch"].includes(
              comparison.status,
            )
          ) {
            comparison.sourceHints = await rankSourceHints({
              actions,
              regions: comparison.regions,
            });
          } else {
            comparison.sourceHints = [];
          }

          visualComparisons.push({
            screenshot: filename,
            ...comparison,
            baseline: path.relative(process.cwd(), comparison.baseline),
            diff: comparison.diff
              ? path.relative(process.cwd(), comparison.diff)
              : null,
            diagnostic: comparison.diagnostic
              ? path.relative(process.cwd(), comparison.diagnostic)
              : null,
          });
        }
      }
        } catch (error) {
          const state = sanitize(step.name || "state");
          const failure = {
            state,
            message: error instanceof Error ? error.message : String(error),
          };
          issues.stepFailures.push(failure);
          const filename = `${viewportName}--${state}--failure.png`;
          try {
            await page.screenshot({ path: path.join(runDir, filename),
              animations: "disabled", caret: "hide", timeout: 5_000 });
            screenshots.push(filename);
          } catch (screenshotError) {
            failure.screenshotError = String(screenshotError);
          }
          break; // Later states may depend on the failed interaction.
        }
      }
    } catch (error) {
      const failure = { state: "navigation", message: error instanceof Error ? error.message : String(error) };
      issues.stepFailures.push(failure);
      const filename = `${viewportName}--navigation--failure.png`;
      try {
        await page.screenshot({ path: path.join(runDir, filename), timeout: 5_000 });
        screenshots.push(filename);
      } catch (screenshotError) {
        failure.screenshotError = String(screenshotError);
      }
    } finally {
      report.results.push({
        viewport: viewportName,
        size: viewport,
        screenshots,
        visualComparisons,
        iconAudit,
        qualityAudit,
        issues,
      });
      await context.close();
    }
  }
} finally {
  await browser.close();
}

if (baselineDir && updateBaselines) {
  const states = captureSteps.map((step) => sanitize(step.name || "state"));
  const expectedPngs = new Set(
    viewportNames.flatMap((viewportName) =>
      states.map((state) => `${viewportName}--${state}.png`),
    ),
  );
  await fs.mkdir(baselineDir, { recursive: true });
  const baselineEntries = await fs.readdir(baselineDir, { withFileTypes: true });
  await Promise.all(
    baselineEntries
      .filter(
        (entry) =>
          entry.isFile() &&
          entry.name.endsWith(".png") &&
          !expectedPngs.has(entry.name),
      )
      .map((entry) => fs.rm(path.join(baselineDir, entry.name), { force: true })),
  );

  const manifest = {
    schemaVersion: 1,
    viewports: viewportNames.map((name) => ({ name, ...VIEWPORTS[name] })),
    states,
    pixelThreshold,
    maxDiffRatio,
  };
  await fs.writeFile(
    path.join(baselineDir, "manifest.json"),
    JSON.stringify(manifest, null, 2) + "\n",
    "utf8",
  );
}

const issueCount = report.results.reduce(
  (total, result) =>
    total +
    result.issues.consoleErrors.length +
    result.issues.pageErrors.length +
    result.issues.requestFailures.length +
    result.issues.badResponses.length +
    result.issues.stepFailures.length,
  0,
);

const visualIssueCount = report.results.reduce(
  (total, result) =>
    total +
    result.visualComparisons.filter((comparison) =>
      ["missing-baseline", "dimension-mismatch", "mismatch"].includes(comparison.status),
    ).length,
  0,
);

let visualAnalysis = null;
let visualAnalysisJsonPath = null;
let visualAnalysisMarkdownPath = null;
if (baselineDir) {
  visualAnalysis = createVisualAnalysis(report);
  visualAnalysisJsonPath = path.join(runDir, "visual-analysis.json");
  visualAnalysisMarkdownPath = path.join(runDir, "visual-analysis.md");
  await fs.writeFile(
    visualAnalysisJsonPath,
    JSON.stringify(visualAnalysis, null, 2) + "\n",
    "utf8",
  );
  await fs.writeFile(
    visualAnalysisMarkdownPath,
    renderVisualAnalysisMarkdown(visualAnalysis) + "\n",
    "utf8",
  );
  report.visual.analysis = {
    status: visualAnalysis.status,
    issueCount: visualAnalysis.issueCount,
    json: path.relative(process.cwd(), visualAnalysisJsonPath),
    markdown: path.relative(process.cwd(), visualAnalysisMarkdownPath),
  };
}

const reportPath = path.join(runDir, "report.json");
await fs.writeFile(reportPath, JSON.stringify(report, null, 2) + "\n", "utf8");

console.log(`UI QA complete: ${runDir}`);
console.log(`Viewports: ${viewportNames.join(", ")}`);
console.log(`Recorded issues: ${issueCount}`);
if (baselineDir) {
  console.log(
    updateBaselines
      ? `Visual baselines updated: ${baselineDir}`
      : `Visual regression issues: ${visualIssueCount}`,
  );
  console.log(
    `Visual analysis: ${path.relative(process.cwd(), visualAnalysisMarkdownPath)}`,
  );
}

if (visualAnalysis?.issueCount > 0) {
  for (const issue of visualAnalysis.issues.slice(0, 5)) {
    const selectors = issue.likelySelectors.slice(0, 3).join(", ") || "n/a";
    const sources =
      issue.sourceHints.slice(0, 3).map((hint) => hint.path).join(", ") || "n/a";
    console.error(
      `[visual] ${issue.viewport}/${issue.state}: ${issue.status}; changed=${issue.mismatchedPixels ?? "n/a"}; selectors=${selectors}; sources=${sources}`,
    );
  }
}

if (hasFlag("--strict") && issueCount + visualIssueCount > 0) {
  process.exitCode = 2;
}
