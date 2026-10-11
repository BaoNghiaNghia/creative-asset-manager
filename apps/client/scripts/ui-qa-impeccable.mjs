/**
 * Read-only bridge: Impeccable detector -> CAM Browser QA quality report.
 *
 * No shell interpolation, no npx downloads, no root or production writes.
 * The detector is an opt-in advisory on existing QA gates.  A mode of
 * "enforce" blocks only new/primary findings after human calibration.
 */
import fs from "node:fs/promises";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
export const BINARY = path.join(ROOT, "integrations/impeccable/node_modules/.bin/impeccable");
const TARGETS = [
  path.join(ROOT, "apps/client/app"),
  path.join(ROOT, "apps/client/styles"),
  path.join(ROOT, "apps/client/layouts"),
];

function parseArgs(args) {
  const get = key => args.includes(key) ? args[args.indexOf(key) + 1] : undefined;
  return {
    mode: get("--mode") ?? process.env.CAM_IMPECCABLE_MODE ?? "advisory",
    output: get("--output") ?? path.join(ROOT, "apps/client/.ui-qa/impeccable-report.json"),
  };
}

function safeSourcePath(value) {
  const raw = String(value ?? "").slice(0, 1024);
  if (!raw) return "";
  const relative = path.relative(ROOT, path.resolve(ROOT, raw));
  if (relative === ".." || relative.startsWith(".." + path.sep) || path.isAbsolute(relative)) {
    return "(outside-workspace)";
  }
  return relative.split(path.sep).join("/");
}

export function normalizeImpeccable(exitCode, stdout, stderr, targets = []) {
  if (![0, 2].includes(exitCode)) {
    return { status: "error", scanned: targets.length, findingCount: 0, findings: [],
      message: "Impeccable detector failed to scan one or more targets.",
      exitCode, stderr: String(stderr ?? "").slice(0, 1000) };
  }
  let parsed;
  try { parsed = JSON.parse(stdout); } catch {
    return { status: "error", scanned: targets.length, findingCount: 0, findings: [],
      message: "Impeccable returned malformed JSON.", exitCode };
  }
  if (!Array.isArray(parsed)) {
    return { status: "error", scanned: targets.length, findingCount: 0, findings: [],
      message: "Impeccable returned an unexpected JSON schema.", exitCode };
  }
  const findings = parsed.slice(0, 500).filter(x => x && typeof x === "object").map(x => ({
    rule: String(x.antipattern ?? x.rule ?? x.ruleId ?? x.id ?? "unspecified").slice(0, 100),
    file: safeSourcePath(x.file ?? x.path ?? x.target),
    line: Number.isInteger(x.line) && x.line >= 0 ? x.line : null,
    message: String(x.description ?? x.message ?? x.reason ?? "").slice(0, 600),
    severity: String(x.severity ?? "primary").slice(0, 24),
    category: String(x.category ?? "").slice(0, 80),
  }));
  return { status: exitCode === 2 || parsed.length ? "findings" : "pass", scanned: targets.length,
    findingCount: parsed.length, findings, truncated: parsed.length > findings.length,
    engineExitCode: exitCode };
}

export function scanDesign({ mode = "advisory", output, runner = spawnSync, binary = BINARY,
  targets = TARGETS, timeoutMs = 120000 } = {}) {
  if (!["off", "advisory", "enforce"].includes(mode)) throw Error("Unknown design QA mode");
  if (mode === "off") return { status: "disabled", mode, findingCount: 0, findings: [] };
  const processResult = runner(binary, ["detect", "--json", ...targets], {
    cwd: ROOT, shell: false, encoding: "utf8", timeout: timeoutMs,
    maxBuffer: 8 * 1024 * 1024,
    env: { ...process.env, CI: "1", NO_COLOR: "1" },
  });
  const report = processResult.error?.code === "ENOENT"
    ? { status: "unavailable", findingCount: 0, findings: [],
        message: "Pinned Impeccable CLI is not installed. Run npm ci --prefix integrations/impeccable --ignore-scripts." }
    : processResult.error
      ? { status: "error", findingCount: 0, findings: [],
          message: String(processResult.error.message).slice(0, 300) }
      : normalizeImpeccable(processResult.status, processResult.stdout, processResult.stderr, targets);
  return { schemaVersion: 1, kind: "impeccable-design-quality", mode,
    generatedAt: new Date().toISOString(), engine: "impeccable@4.5.2", ...report };
}

async function main() {
  const { mode, output } = parseArgs(process.argv.slice(2));
  const report = scanDesign({ mode });
  if (!path.resolve(output).startsWith(path.join(ROOT, "apps/client/.ui-qa") + path.sep)) {
    throw Error("Design QA report output must be within .ui-qa");
  }
  await fs.mkdir(path.dirname(output), { recursive: true });
  await fs.writeFile(output, JSON.stringify(report, null, 2) + "\n", "utf8");
  console.log("Impeccable design QA:", report.status, "findings=" + report.findingCount,
    "report=" + path.relative(ROOT, output));
  if (report.status === "error") process.exitCode = 1;
  if (mode === "enforce" && (report.status === "unavailable" || report.status === "findings")) process.exitCode = 2;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(e => { console.error("Design QA:", e.message); process.exitCode = 1; });
}
