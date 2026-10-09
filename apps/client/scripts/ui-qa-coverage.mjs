/** Non-mutating inventory of visual baselines vs interaction plans. */
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { UI_QA_PROFILES } from "./ui-qa-profiles.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const unique = values => [...new Set(values)];

export function analyzeBaselineCoverage(plan, manifest, profile) {
  const plannedStates = unique((plan.steps || []).map(step => String(step.name || "default")));
  const plannedViewports = unique(plan.viewports || []);
  const acceptedStates = new Set(manifest?.states || []);
  const acceptedViewports = new Set((manifest?.viewports || []).map(v => typeof v === "string" ? v : v.name));
  const missingStates = plannedStates.filter(state => !acceptedStates.has(state));
  const missingViewports = plannedViewports.filter(viewport => !acceptedViewports.has(viewport));
  const obsoleteStates = [...acceptedStates].filter(state => !plannedStates.includes(state));
  return {
    profile, plannedStates: plannedStates.length, acceptedStates: acceptedStates.size,
    plannedViewports: plannedViewports.length, acceptedViewports: acceptedViewports.size,
    missingStates, missingViewports, obsoleteStates,
    status: !manifest ? "missing-manifest" : missingStates.length || missingViewports.length ? "stale" : "aligned",
  };
}

export async function collectVisualCoverage(root = ROOT) {
  const profiles = [];
  for (const [name, cfg] of Object.entries(UI_QA_PROFILES)) {
    const plan = JSON.parse(await fs.readFile(path.join(root, cfg.plan), "utf8"));
    let manifest;
    try { manifest = JSON.parse(await fs.readFile(path.join(root, cfg.baseline, "manifest.json"), "utf8")); }
    catch (error) { if (error.code !== "ENOENT") throw error; manifest = null; }
    profiles.push(analyzeBaselineCoverage(plan, manifest, name));
  }
  return {
    schemaVersion: 1,
    profiles,
    summary: {
      total: profiles.length,
      aligned: profiles.filter(p => p.status === "aligned").length,
      stale: profiles.filter(p => p.status !== "aligned").length,
      uncoveredStates: profiles.reduce((n, p) => n + p.missingStates.length, 0),
      uncoveredViewports: profiles.reduce((n, p) => n + p.missingViewports.length, 0),
    },
  };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const result = await collectVisualCoverage();
  const output = process.argv.find(a => a.startsWith("--output="))?.slice(9);
  if (output) {
    await fs.mkdir(path.dirname(path.resolve(output)), { recursive: true });
    await fs.writeFile(output, JSON.stringify(result, null, 2) + "\n", "utf8");
  }
  for (const profile of result.profiles) {
    console.log(`${profile.status.toUpperCase()} ${profile.profile}: ${profile.acceptedStates}/${profile.plannedStates} states; missing=[${profile.missingStates.join(",")}]`);
  }
  console.log(`Visual QA coverage: ${result.summary.aligned}/${result.summary.total} aligned; missing states ${result.summary.uncoveredStates}`);
  // Do not update baselines automatically. An explicit accepted proposal is required.
  if (process.argv.includes("--strict") && result.summary.stale) process.exitCode = 2;
}
