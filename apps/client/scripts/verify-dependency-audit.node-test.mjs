import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const script = fileURLToPath(new URL("./verify-dependency-audit.mjs", import.meta.url));
function check(vulnerabilities, details = {}) {
  return spawnSync(process.execPath, [script], {
    input: JSON.stringify({ metadata: { vulnerabilities }, vulnerabilities: details }),
    encoding: "utf8",
  });
}

test("moderate advisories are reported but do not block deployment", () => {
  const run = check({ critical: 0, high: 0, moderate: 28, low: 0 });
  assert.equal(run.status, 0);
  assert.match(run.stdout, /PASS: no high or critical/);
});
test("high or critical advisories block deployment", () => {
  const run = check({ critical: 0, high: 1, moderate: 28, low: 0 },
    { "example-package": { name: "example-package", severity: "high" } });
  assert.equal(run.status, 1);
  assert.match(run.stderr, /BLOCK: example-package \(high\)/);
});
test("missing metadata fails closed", () => {
  const run = spawnSync(process.execPath, [script], {
    input: JSON.stringify({ error: { summary: "registry unavailable" } }),
    encoding: "utf8",
  });
  assert.equal(run.status, 1);
  assert.match(run.stderr, /Dependency audit failed/);
});
