import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const script = fileURLToPath(new URL("./verify-dependency-audit.mjs", import.meta.url));
function check(vulnerabilities, details = {}, args = []) {
  return spawnSync(process.execPath, [script, ...args], {
    input: JSON.stringify({ metadata: { vulnerabilities }, vulnerabilities: details }),
    encoding: "utf8",
  });
}

test("default high threshold permits moderate advisories for backward compatibility", () => {
  const run = check({ critical: 0, high: 0, moderate: 28, low: 0 });
  assert.equal(run.status, 0);
  assert.match(run.stdout, /PASS: no npm advisories at or above high/);
});
test("strict CI threshold rejects moderate advisories", () => {
  const run = check({ critical: 0, high: 0, moderate: 1, low: 0 },
    { "example-package": { name: "example-package", severity: "moderate" } },
    ["--minimum-severity=moderate"]);
  assert.equal(run.status, 1);
  assert.match(run.stderr, /BLOCK: example-package \(moderate\)/);
});
test("high or critical advisories block deployment", () => {
  const run = check({ critical: 0, high: 1, moderate: 0, low: 0 },
    { "example-package": { name: "example-package", severity: "high" } });
  assert.equal(run.status, 1);
  assert.match(run.stderr, /BLOCK: example-package \(high\)/);
});
test("zero vulnerabilities pass strict CI threshold", () => {
  const run = check({ critical: 0, high: 0, moderate: 0, low: 0 },
    {}, ["--minimum-severity=moderate"]);
  assert.equal(run.status, 0);
  assert.match(run.stdout, /PASS: no npm advisories at or above moderate/);
});
test("unknown arguments fail closed", () => {
  const run = check({ critical: 0, high: 0, moderate: 0, low: 0 },
    {}, ["--minimum-severity=invalid"]);
  assert.equal(run.status, 1);
  assert.match(run.stderr, /Unknown audit gate arguments/);
});
test("missing metadata fails closed", () => {
  const run = spawnSync(process.execPath, [script], {
    input: JSON.stringify({ error: { summary: "registry unavailable" } }),
    encoding: "utf8",
  });
  assert.equal(run.status, 1);
  assert.match(run.stderr, /Dependency audit failed/);
});
