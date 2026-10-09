/** Summarize a *single* local fixture QA matrix without confusing old runs for new passes. */
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { getUiQaProfile } from "./ui-qa-profiles.mjs";
import { collectVisualCoverage } from "./ui-qa-coverage.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const ISSUE_KEYS = ["consoleErrors", "pageErrors", "requestFailures", "badResponses", "stepFailures"];
const arg = name => {
  const index = process.argv.indexOf(name);
  return index < 0 ? "" : process.argv[index + 1] || "";
};

export function summarizeFixtureReport(report, profileName, expectedViewports = []) {
  const resultViewports = (report.results || []).map(r => r.viewport);
  const missingViewports = expectedViewports.filter(viewport => !resultViewports.includes(viewport));
  const issues = (report.results || []).reduce((sum, result) => sum +
    ISSUE_KEYS.reduce((n, key) => n + (result.issues?.[key]?.length || 0), 0), 0);
  const incomplete = (report.results || []).filter(result =>
    !Array.isArray(result.screenshots) || result.screenshots.length !== (report.states || []).length)
    .map(result => result.viewport);
  const missingQualityViewports = (report.results || []).filter(result =>
    !Array.isArray(result.qualityAudit) || result.qualityAudit.length !== (report.states || []).length)
    .map(result => result.viewport);
  const quality = (report.results || []).flatMap(result =>
    (result.qualityAudit || []).map(q => ({ viewport: result.viewport, ...q })));
  const max = key => quality.length ? Math.max(...quality.map(q => Number(q[key] || 0))) : null;
  const status = report.results?.length && !issues && !missingViewports.length &&
    !incomplete.length && !missingQualityViewports.length && (report.states || []).length
    ? "pass" : "fail";
  return {
    profile: profileName,
    status, runId: report.runId, browser: report.browser || null,
    viewports: resultViewports,
    states: (report.states || []).length,
    issueCount: issues,
    missingViewports, incompleteViewports: incomplete, missingQualityViewports,
    quality: { maxViewportOverflowPx: max("viewportOverflowPx"), maxSmallTargets: max("smallTargets"),
      maxUnnamedControls: max("unnamedControls"), maxMissingAlt: max("missingAlt"),
      maxResources: max("resourceCount"), maxResourceTransferKb: max("resourceTransferKb") },
  };
}

export async function collectQaMatrix({ profiles, since, root = ROOT, viewports = [] }) {
  const qaDir = path.join(root, "apps/client/.ui-qa");
  const entries = (await fs.readdir(qaDir, { withFileTypes: true }))
    .filter(dir => dir.isDirectory() && /^20\d\d-/.test(dir.name))
    .map(dir => dir.name).sort().reverse();
  const reports = [];
  for (const name of entries) {
    const file = path.join(qaDir, name, "report.json");
    try {
      const fileInfo = await fs.stat(file);
      if (fileInfo.mtimeMs < since) continue;
      const report = JSON.parse(await fs.readFile(file, "utf8"));
      reports.push(report);
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
  }
  const rows = profiles.map(profileName => {
    const profile = getUiQaProfile(profileName);
    const report = reports.find(r => {
      try { return new URL(r.url).pathname === profile.route; }
      catch { return false; }
    });
    return report ? summarizeFixtureReport(report, profileName, viewports)
      : { profile: profileName, status: "missing", reason: "No QA report from this matrix run" };
  });
  const baseline = await collectVisualCoverage(root);
  return {
    schemaVersion: 1, kind: "fixture-browser-qa",
    generatedAt: new Date().toISOString(), profiles: rows,
    summary: { total: rows.length, passed: rows.filter(r => r.status === "pass").length,
      failed: rows.filter(r => r.status !== "pass").length,
      baselineProfilesAligned: baseline.summary.aligned,
      baselineProfilesTotal: baseline.summary.total,
      baselineStatesAwaitingApproval: baseline.summary.uncoveredStates },
    note: "Browser QA fixture outcomes do not confirm authenticated production health or remote CI success.",
  };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const profiles = arg("--profiles").split(",").map(s => s.trim()).filter(Boolean);
  if (!profiles.length) throw new Error("Specify --profiles for the current QA matrix.");
  const since = Number(arg("--since"));
  if (!Number.isFinite(since) || since <= 0) throw new Error("Specify --since epoch seconds.");
  const viewports = arg("--viewports").split(",").map(s => s.trim()).filter(Boolean);
  const report = await collectQaMatrix({ profiles, since: since * 1000, viewports });
  const output = arg("--output");
  if (output) {
    await fs.mkdir(path.dirname(path.resolve(output)), { recursive: true });
    await fs.writeFile(output, JSON.stringify(report, null, 2) + "\n", "utf8");
  }
  console.log(`Fixture Browser QA: ${report.summary.passed}/${report.summary.total} passed; ` +
    `${report.summary.baselineStatesAwaitingApproval} states await baseline approval`);
  for (const row of report.profiles) {
    console.log(`${row.status.toUpperCase()} ${row.profile} (${row.viewports?.join(",") || row.reason || "unknown"})`);
  }
  if (report.summary.failed) process.exitCode = 2;
}
