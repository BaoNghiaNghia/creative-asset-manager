/** Read-only visual-baseline review packet. Never writes tracked baselines. */
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { getUiQaProfile } from "./ui-qa-profiles.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const PNG_NAME = /^[a-zA-Z0-9_-]+--[a-zA-Z0-9_-]+\.png$/;
const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, char =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
const arg = flag => {
  const i = process.argv.indexOf(flag);
  return i < 0 ? "" : process.argv[i + 1] || "";
};

export function buildVisualReviewRows(report, manifest) {
  const approved = new Set(manifest.states || []);
  const rows = [];
  const seen = new Set();
  for (const result of report.results || []) {
    for (const screenshot of result.screenshots || []) {
      if (!PNG_NAME.test(screenshot) || seen.has(screenshot)) {
        throw new Error("Invalid or repeated Browser QA screenshot: " + screenshot);
      }
      seen.add(screenshot);
      const prefix = result.viewport + "--";
      if (!screenshot.startsWith(prefix)) {
        throw new Error("Screenshot does not match viewport: " + screenshot);
      }
      const state = screenshot.slice(prefix.length, -4);
      rows.push({
        viewport: result.viewport,
        state, screenshot,
        approvedState: approved.has(state),
        status: approved.has(state) ? "compare-with-accepted" : "needs-first-approval",
      });
    }
  }
  return rows;
}

export async function findLatestProfileRun(profileName, root = ROOT) {
  const profile = getUiQaProfile(profileName);
  const qaRoot = path.resolve(root, "apps/client/.ui-qa");
  const entries = (await fs.readdir(qaRoot, { withFileTypes: true }))
    .filter(entry => entry.isDirectory() && /^20\d\d-/.test(entry.name))
    .map(entry => entry.name).sort().reverse();
  for (const name of entries) {
    const runDir = path.join(qaRoot, name);
    try {
      const report = JSON.parse(await fs.readFile(path.join(runDir, "report.json"), "utf8"));
      if (new URL(report.url).pathname === profile.route && report.results?.length) return runDir;
    } catch (error) {
      if (error.code !== "ENOENT") continue;
    }
  }
  throw new Error("No completed Browser QA run found for " + profileName);
}

export async function createVisualReviewPacket({ profileName, runDir, root = ROOT }) {
  const profile = getUiQaProfile(profileName);
  const qaRoot = path.resolve(root, "apps/client/.ui-qa");
  const actualRun = path.resolve(runDir);
  if (!actualRun.startsWith(qaRoot + path.sep)) {
    throw new Error("Review packet must stay inside apps/client/.ui-qa");
  }
  const report = JSON.parse(await fs.readFile(path.join(actualRun, "report.json"), "utf8"));
  const baselineDir = path.join(root, profile.baseline);
  const manifest = JSON.parse(await fs.readFile(path.join(baselineDir, "manifest.json"), "utf8"));
  const rows = buildVisualReviewRows(report, manifest);
  if (!rows.length) throw new Error("No captured screenshots available for review.");
  const reviewDir = path.join(actualRun, "review-accepted");
  await fs.mkdir(reviewDir, { recursive: true });
  for (const row of rows) {
    const candidate = path.join(actualRun, row.screenshot);
    await fs.access(candidate);
    const baseline = path.join(baselineDir, row.screenshot);
    try {
      await fs.copyFile(baseline, path.join(reviewDir, row.screenshot));
      row.hasApprovedImage = true;
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
      row.hasApprovedImage = false;
    }
  }
  const pending = [...new Set(rows.filter(row => !row.approvedState).map(row => row.state))];
  const summary = {
    schemaVersion: 1, profile: profileName, runId: report.runId,
    browser: report.browser || null,
    status: "review-only", approvedStates: manifest.states || [],
    pendingStates: pending, screenshots: rows,
    warning: "This packet is for human review. It does not approve or modify tracked visual baselines.",
  };
  await fs.writeFile(path.join(actualRun, "visual-review.json"), JSON.stringify(summary, null, 2) + "\n");

  const content = rows.map(row => `
<section class="item">
  <h3>${escapeHtml(row.viewport)} · ${escapeHtml(row.state)}</h3>
  <p class="state">${row.approvedState ? "Existing approved state" : "New state · approval required"}</p>
  <div class="images">
    <figure><figcaption>Current fixture capture</figcaption>
      <img loading="lazy" alt="Current capture of ${escapeHtml(row.state)}" src="./${row.screenshot}"></figure>
    <figure><figcaption>Last accepted baseline</figcaption>
      ${row.hasApprovedImage
        ? `<img loading="lazy" alt="Accepted baseline of ${escapeHtml(row.state)}" src="./review-accepted/${row.screenshot}">`
        : '<p class="missing">No accepted image exists for this state and viewport.</p>'}
    </figure>
  </div>
</section>`).join("\n");
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Visual QA Review · ${escapeHtml(profileName)}</title>
<style>
:root{font-family:system-ui,sans-serif;color-scheme:light;background:#f6f8fb;color:#25334a}
body{margin:0 auto;max-width:1480px;padding:24px}
h1{font-size:25px}h2{font-size:18px}h3{font-size:15px;margin:0}
p{line-height:1.5}.intro{color:#53647d}
.item{background:white;border:1px solid #dce3ee;border-radius:12px;padding:16px;margin:16px 0}
.state{font-size:12px;color:#62758d}.images{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
figure{margin:0;min-width:0}figcaption{font-size:12px;font-weight:700;margin:0 0 8px}
img{width:100%;height:auto;display:block;border:1px solid #e2e7f0;border-radius:8px}
.missing{padding:28px 18px;border:1px dashed #b4c1d3;border-radius:8px;color:#66758c}
@media(max-width:700px){body{padding:12px}.images{grid-template-columns:minmax(0,1fr)}}
</style></head><body>
<h1>Visual QA review · ${escapeHtml(profileName)}</h1>
<p class="intro">Run: ${escapeHtml(report.runId)} · Browser: ${escapeHtml(report.browser?.engine || "unknown")} ·
${rows.length} captures · ${pending.length} states without approved baselines.</p>
<p><strong>Review only.</strong> No baseline is accepted or modified. Inspect mobile, tablet, and desktop screenshots before explicitly approving design changes.</p>
${content}</body></html>`;
  await fs.writeFile(path.join(actualRun, "visual-review.html"), html);
  return { ...summary, htmlPath: path.join(actualRun, "visual-review.html") };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const profileName = arg("--profile");
  if (!profileName) throw new Error("Provide --profile and --run-dir or --latest.");
  const runDir = arg("--run-dir") || (process.argv.includes("--latest")
    ? await findLatestProfileRun(profileName) : "");
  if (!runDir) throw new Error("Provide --run-dir or --latest.");
  const result = await createVisualReviewPacket({ profileName, runDir });
  console.log(JSON.stringify({ status: result.status, browser: result.browser,
    screenshots: result.screenshots.length, pendingStates: result.pendingStates,
    htmlPath: result.htmlPath }, null, 2));
}
