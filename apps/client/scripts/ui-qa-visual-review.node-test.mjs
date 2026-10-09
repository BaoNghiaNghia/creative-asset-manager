import assert from "node:assert/strict";
import test from "node:test";
import { buildVisualReviewRows } from "./ui-qa-visual-review.mjs";

test("visual review marks new states without modifying the accepted manifest", () => {
  const manifest = { states: ["default"] };
  const before = JSON.stringify(manifest);
  const rows = buildVisualReviewRows({
    results: [{ viewport: "mobile", screenshots: [
      "mobile--default.png", "mobile--stage1-actions.png",
    ] }],
  }, manifest);
  assert.equal(rows.length, 2);
  assert.equal(rows[0].status, "compare-with-accepted");
  assert.equal(rows[1].status, "needs-first-approval");
  assert.equal(rows[1].state, "stage1-actions");
  assert.equal(JSON.stringify(manifest), before);
});

test("visual review rejects unsafe or duplicated screenshot paths", () => {
  assert.throws(() => buildVisualReviewRows({
    results: [{ viewport: "mobile", screenshots: ["../other.png"] }],
  }, { states: [] }), /Invalid or repeated/);
  assert.throws(() => buildVisualReviewRows({
    results: [{ viewport: "mobile", screenshots: ["desktop--default.png"] }],
  }, { states: [] }), /does not match viewport/);
  assert.throws(() => buildVisualReviewRows({
    results: [{ viewport: "mobile", screenshots: ["mobile--default.png", "mobile--default.png"] }],
  }, { states: [] }), /Invalid or repeated/);
});
