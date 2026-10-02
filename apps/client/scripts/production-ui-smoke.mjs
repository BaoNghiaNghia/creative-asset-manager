import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const CLIENT_ROOT = path.resolve(SCRIPT_DIR, "..");
const REPO_ROOT = path.resolve(CLIENT_ROOT, "../..");
const READ_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);
const DEFAULT_TIMEOUT_MS = 15_000;

function argValue(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : undefined;
}

function hasFlag(name) {
  return process.argv.includes(name);
}

function sanitize(value) {
  return String(value || "state")
    .trim()
    .replace(/[^A-Za-z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .toLowerCase() || "state";
}

function normalizeText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function nowId() {
  return new Date().toISOString().replace(/[:.]/g, "-");
}

function isSameOrigin(url, origin) {
  try {
    return new URL(url).origin === origin;
  } catch {
    return false;
  }
}

function selectedViewports(plan, csv) {
  const all = new Map((plan.viewports || []).map((item) => [item.name, item]));
  const requested = csv
    ? csv.split(",").map((item) => item.trim()).filter(Boolean)
    : [...all.keys()];
  if (!requested.length) throw new Error("No production smoke viewports selected.");
  return requested.map((name) => {
    const viewport = all.get(name);
    if (!viewport) throw new Error(`Unknown production smoke viewport: ${name}`);
    return viewport;
  });
}

function ensureProductionUrl(value) {
  const url = new URL(value);
  if (url.protocol !== "https:") {
    throw new Error("Production UI smoke requires an HTTPS base URL.");
  }
  if (["127.0.0.1", "localhost", "::1"].includes(url.hostname)) {
    throw new Error("Production UI smoke refuses loopback/local URLs.");
  }
  return new URL(url.href.replace(/\/+$/, "") + "/");
}

function ensureStorageStateOutsideRepo(storageState) {
  if (!storageState) return null;
  const resolved = path.resolve(storageState);
  const relative = path.relative(REPO_ROOT, resolved);
  if (!relative.startsWith("..") && !path.isAbsolute(relative)) {
    throw new Error(
      "Production storage state must live outside the repository because it contains session credentials.",
    );
  }
  return resolved;
}

async function loadPlan(planPath) {
  const plan = JSON.parse(await fs.readFile(planPath, "utf8"));
  if (plan.schemaVersion !== 1 || !Array.isArray(plan.routes) || !plan.routes.length) {
    throw new Error("Invalid Production UI smoke plan.");
  }
  return plan;
}

async function fetchBuildInfo(context, baseUrl) {
  const response = await context.request.get(new URL("/build-info.json", baseUrl).href, {
    failOnStatusCode: false,
  });
  const status = response.status();
  if (status < 200 || status >= 300) {
    throw new Error(`Production build-info.json returned HTTP ${status}`);
  }
  const data = await response.json();
  if (!data || typeof data.build_commit !== "string" || !/^[0-9a-f]{7,64}$/i.test(data.build_commit)) {
    throw new Error("Production build-info.json has no valid build_commit.");
  }
  return data;
}

async function runState(page, state, routeName, viewportName, runDir, routeIssues) {
  const stateName = sanitize(state.name || "default");
  try {
    if (state.waitFor) {
      await page.locator(state.waitFor).first().waitFor({
        state: "visible",
        timeout: state.timeoutMs || 5_000,
      });
    }
    if (state.focus) {
      const target = page.locator(state.focus).first();
      const count = await target.count();
      if (!count) {
        if (!state.optional) throw new Error(`Missing focus selector: ${state.focus}`);
      } else {
        await target.focus();
      }
    }
    if (state.hover) {
      const target = page.locator(state.hover).first();
      const count = await target.count();
      if (!count) {
        if (!state.optional) throw new Error(`Missing hover selector: ${state.hover}`);
      } else {
        await target.hover();
      }
    }
    if (state.waitMs) await page.waitForTimeout(state.waitMs);
  } catch (error) {
    routeIssues.push({
      kind: "state",
      state: stateName,
      message: error instanceof Error ? error.message : String(error),
    });
  }

  const filename = `${sanitize(viewportName)}--${sanitize(routeName)}--${stateName}.png`;
  await page.screenshot({
    path: path.join(runDir, filename),
    fullPage: false,
    animations: "disabled",
    caret: "hide",
  });
  return filename;
}

async function main() {
  const baseUrl = ensureProductionUrl(argValue("--url") || process.env.CAM_PRODUCTION_UI_URL || "");
  const planPath = path.resolve(
    argValue("--plan") ||
      process.env.CAM_PRODUCTION_UI_PLAN ||
      path.join(REPO_ROOT, "docs/operations/production-ui-smoke-plan.json"),
  );
  const outputRoot = path.resolve(
    argValue("--output") ||
      process.env.CAM_PRODUCTION_UI_OUTPUT ||
      path.join(CLIENT_ROOT, ".ui-qa/production-smoke"),
  );
  const storageState = ensureStorageStateOutsideRepo(
    argValue("--storage-state") || process.env.CAM_PRODUCTION_UI_STORAGE_STATE || "",
  );
  const publicOnly =
    hasFlag("--public-only") || process.env.CAM_PRODUCTION_UI_PUBLIC_ONLY === "1";
  const strict = !hasFlag("--no-strict");
  const timeoutMs = Number(argValue("--timeout-ms") || DEFAULT_TIMEOUT_MS);
  const plan = await loadPlan(planPath);
  const viewports = selectedViewports(
    plan,
    argValue("--viewports") || process.env.CAM_PRODUCTION_UI_VIEWPORTS,
  );
  const routes = plan.routes.filter((route) => publicOnly ? route.public === true : true);

  if (!routes.length) throw new Error("Production UI smoke plan selected no routes.");
  if (!publicOnly && routes.some((route) => route.requiresAuth) && !storageState) {
    throw new Error(
      "Authenticated Production UI smoke requires CAM_PRODUCTION_UI_STORAGE_STATE. Use public-only mode only for a deliberately partial smoke.",
    );
  }

  if (storageState) await fs.access(storageState);

  const runDir = path.join(outputRoot, nowId());
  await fs.mkdir(runDir, { recursive: true });

  const browser = await chromium.launch({
    channel: "chrome",
    headless: true,
    chromiumSandbox: false,
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });

  const report = {
    schemaVersion: 1,
    mode: publicOnly ? "production-public-readonly" : "production-authenticated-readonly",
    baseUrl: baseUrl.origin,
    createdAt: new Date().toISOString(),
    plan: path.relative(REPO_ROOT, planPath),
    viewports: viewports.map(({ name, width, height }) => ({ name, width, height })),
    routes: [],
    buildInfo: null,
    summary: null,
  };

  try {
    const buildContext = await browser.newContext({
      storageState: storageState || undefined,
      ignoreHTTPSErrors: false,
    });
    report.buildInfo = await fetchBuildInfo(buildContext, baseUrl);
    await buildContext.close();

    for (const viewport of viewports) {
      const context = await browser.newContext({
        viewport: { width: viewport.width, height: viewport.height },
        storageState: storageState || undefined,
        ignoreHTTPSErrors: false,
      });

      const blockedMutations = [];
      await context.route("**/*", async (route) => {
        const request = route.request();
        if (!READ_METHODS.has(request.method().toUpperCase())) {
          blockedMutations.push({
            method: request.method(),
            url: request.url(),
            resourceType: request.resourceType(),
          });
          await route.abort("blockedbyclient");
          return;
        }
        await route.continue();
      });

      for (const routePlan of routes) {
        const page = await context.newPage();
        const issues = [];
        const warnings = [];
        const screenshots = [];
        const routeRecord = {
          viewport: viewport.name,
          route: routePlan.name,
          path: routePlan.path,
          requiresAuth: Boolean(routePlan.requiresAuth),
          status: "pass",
          finalUrl: null,
          screenshots,
          issues,
          warnings,
        };

        page.on("console", (message) => {
          if (message.type() === "error") {
            issues.push({ kind: "console", message: normalizeText(message.text()) });
          }
        });
        page.on("pageerror", (error) => {
          issues.push({ kind: "pageerror", message: normalizeText(error.message) });
        });
        page.on("requestfailed", (request) => {
          const error = request.failure()?.errorText || "request_failed";
          if (READ_METHODS.has(request.method()) && error === "net::ERR_ABORTED") {
            warnings.push({ kind: "request-aborted", method: request.method(), url: request.url() });
            return;
          }
          const target = isSameOrigin(request.url(), baseUrl.origin) ? issues : warnings;
          target.push({
            kind: "request-failed",
            method: request.method(),
            url: request.url(),
            error,
          });
        });
        page.on("response", (response) => {
          if (response.status() < 400) return;
          const target = isSameOrigin(response.url(), baseUrl.origin) ? issues : warnings;
          target.push({
            kind: "bad-response",
            status: response.status(),
            url: response.url(),
          });
        });

        const mutationStart = blockedMutations.length;
        const targetUrl = new URL(routePlan.path, baseUrl).href;
        try {
          const response = await page.goto(targetUrl, {
            waitUntil: "domcontentloaded",
            timeout: timeoutMs,
          });
          if (!response || response.status() >= 400) {
            issues.push({
              kind: "document",
              status: response?.status() ?? null,
              message: "Route navigation did not return a successful document response.",
            });
          }
          await page.locator("html[data-cam-ready='1']").waitFor({
            state: "attached",
            timeout: timeoutMs,
          });
          if (routePlan.ready) {
            await page.locator(routePlan.ready).first().waitFor({
              state: "visible",
              timeout: timeoutMs,
            });
          }
          routeRecord.finalUrl = page.url();
          if (new URL(page.url()).origin !== baseUrl.origin) {
            issues.push({
              kind: "redirect",
              message: `Route left Production origin: ${page.url()}`,
            });
          }

          const bodyText = normalizeText(await page.locator("body").innerText());
          for (const forbidden of routePlan.forbiddenText || []) {
            if (bodyText.toLowerCase().includes(String(forbidden).toLowerCase())) {
              issues.push({
                kind: "forbidden-text",
                message: `Unexpected auth/permission state: ${forbidden}`,
              });
            }
          }

          const states = routePlan.states?.length ? routePlan.states : [{ name: "default" }];
          for (const state of states) {
            screenshots.push(
              await runState(page, state, routePlan.name, viewport.name, runDir, issues),
            );
          }
        } catch (error) {
          issues.push({
            kind: "route",
            message: error instanceof Error ? error.message : String(error),
          });
          try {
            screenshots.push(
              await runState(page, { name: "failure" }, routePlan.name, viewport.name, runDir, issues),
            );
          } catch {
            // Navigation may have failed before a renderable page existed.
          }
        }

        const routeMutations = blockedMutations.slice(mutationStart);
        if (routeMutations.length) {
          issues.push({
            kind: "blocked-mutation",
            message: "Production smoke blocked one or more non-read HTTP requests.",
            requests: routeMutations,
          });
        }

        if (issues.length) routeRecord.status = "fail";
        report.routes.push(routeRecord);
        await page.close();
      }

      await context.close();
    }
  } finally {
    await browser.close();
  }

  const failedRoutes = report.routes.filter((route) => route.status === "fail").length;
  const issueCount = report.routes.reduce((sum, route) => sum + route.issues.length, 0);
  const warningCount = report.routes.reduce((sum, route) => sum + route.warnings.length, 0);
  report.summary = {
    status: failedRoutes === 0 ? "pass" : "fail",
    routeChecks: report.routes.length,
    failedRoutes,
    issueCount,
    warningCount,
  };

  await fs.writeFile(
    path.join(runDir, "report.json"),
    JSON.stringify(report, null, 2) + "\n",
    "utf8",
  );

  const lines = [
    "# Production UI smoke",
    "",
    `Status: **${report.summary.status.toUpperCase()}**`,
    `Mode: ${report.mode}`,
    `Base URL: ${report.baseUrl}`,
    `Build commit: ${report.buildInfo?.build_commit || "unknown"}`,
    `Route checks: ${report.summary.routeChecks}`,
    `Failed routes: ${report.summary.failedRoutes}`,
    `Issues: ${report.summary.issueCount}`,
    `Warnings: ${report.summary.warningCount}`,
    "",
  ];
  for (const route of report.routes) {
    lines.push(
      `- ${route.status === "pass" ? "PASS" : "FAIL"} — ${route.viewport} / ${route.route} (${route.path}) — issues=${route.issues.length}, warnings=${route.warnings.length}`,
    );
  }
  lines.push(
    "",
    "Safety: this smoke blocks every HTTP method except GET, HEAD, and OPTIONS.",
    "No Production create/update/delete action is permitted by this workflow.",
    "",
  );
  await fs.writeFile(path.join(runDir, "report.md"), lines.join("\n"), "utf8");

  console.log(`Production UI smoke complete: ${runDir}`);
  console.log(`Status: ${report.summary.status}`);
  console.log(`Build commit: ${report.buildInfo?.build_commit || "unknown"}`);
  console.log(`Route checks: ${report.summary.routeChecks}; issues: ${issueCount}; warnings: ${warningCount}`);

  if (strict && failedRoutes > 0) process.exitCode = 2;
}

await main();
