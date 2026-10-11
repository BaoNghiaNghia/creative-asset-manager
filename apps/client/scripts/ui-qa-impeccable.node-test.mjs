import test from "node:test";
import assert from "node:assert/strict";
import { normalizeImpeccable, scanDesign } from "./ui-qa-impeccable.mjs";

test("missing pinned CLI becomes unavailable without npm download", () => {
  let invoked = null;
  const result = scanDesign({
    mode: "advisory", runner(binary, args, options) {
      invoked = { binary, args, options };
      return { error: Object.assign(new Error("ENOENT"), { code: "ENOENT" }) };
    },
  });
  assert.equal(result.status, "unavailable");
  assert.equal(result.mode, "advisory");
  assert.equal(invoked.options.shell, false);
  assert.deepEqual(invoked.args.slice(0, 2), ["detect", "--json"]);
});

test("detector findings are normalized without losing rules or source line", () => {
  const input = [{ antipattern: "side-tab", file: "./ui.css", line: 18,
    description: "Bad accent border", category: "slop", severity: "warning" }];
  const report = normalizeImpeccable(2, JSON.stringify(input), "", ["ui.css"]);
  assert.equal(report.status, "findings");
  assert.equal(report.findingCount, 1);
  assert.equal(report.findings[0].rule, "side-tab");
  assert.equal(report.findings[0].line, 18);
  assert.equal(report.findings[0].category, "slop");
  assert.equal(report.findings[0].file, "ui.css");
});

test("nonempty findings with success exit are never reported as a pass", () => {
  const result = normalizeImpeccable(0, JSON.stringify([{
    rule: "broken-image", file: "/etc/passwd", description: "Example finding",
  }]), "", ["app"]);
  assert.equal(result.status, "findings");
  assert.equal(result.findings[0].file, "(outside-workspace)");
});

test("partial scans and malformed JSON fail closed", () => {
  assert.equal(normalizeImpeccable(1, "[]", "", ["ui.css"]).status, "error");
  assert.equal(normalizeImpeccable(0, "bad", "", ["ui.css"]).status, "error");
  assert.equal(normalizeImpeccable(0, "{}", "", ["ui.css"]).status, "error");
});

test("disabled design scanning never invokes the subprocess", () => {
  let called = false;
  const result = scanDesign({ mode: "off", runner() { called = true; } });
  assert.equal(result.status, "disabled");
  assert.equal(called, false);
});
