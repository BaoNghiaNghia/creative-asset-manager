import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { summarizeFixtureReport } from "./ui-qa-matrix-summary.mjs";

const sample = {
  runId: "test-one", states: ["default", "filter"],
  results: [{ viewport: "mobile", screenshots: ["mobile--default.png", "mobile--filter.png"],
    issues: { consoleErrors: [], pageErrors: [], requestFailures: [], badResponses: [], stepFailures: [] },
    qualityAudit: [
      { state: "default", viewportOverflowPx: 0, missingAlt: 0, smallTargets: 1,
        unnamedControls: 0, resourceCount: 12, resourceTransferKb: 200 },
      { state: "filter", viewportOverflowPx: 0, missingAlt: 0, smallTargets: 0,
        unnamedControls: 0, resourceCount: 15, resourceTransferKb: 270 },
    ] }],
};

test("matrix row measures QA completeness and observable quality", () => {
  const result = summarizeFixtureReport(sample, "realistic-review-ugc", ["mobile"]);
  assert.equal(result.status, "pass");
  assert.equal(result.states, 2);
  assert.equal(result.quality.maxSmallTargets, 1);
  assert.equal(result.quality.maxResources, 15);
});

test("missing viewport, screenshots or step failure cannot be called PASS", () => {
  const missingViewport = summarizeFixtureReport(sample, "realistic-review-ugc", ["desktop", "mobile"]);
  assert.equal(missingViewport.status, "fail");
  assert.deepEqual(missingViewport.missingViewports, ["desktop"]);
  const missingState = structuredClone(sample);
  missingState.results[0].screenshots.pop();
  assert.equal(summarizeFixtureReport(missingState, "realistic-review-ugc", ["mobile"]).status, "fail");
  const bad = structuredClone(sample);
  bad.results[0].issues.stepFailures.push({ state: "filter", message: "Missing icon" });
  const result = summarizeFixtureReport(bad, "realistic-review-ugc", ["mobile"]);
  assert.equal(result.status, "fail");
  assert.equal(result.issueCount, 1);
});

test("visual differences cannot be presented as an overall PASS", () => {
  const mismatched = structuredClone(sample);
  mismatched.visual = {
    baselineDir: "visual-baselines/job-queue",
    analysis: { status: "failed", issueCount: 4 },
  };
  const result = summarizeFixtureReport(mismatched, "job-queue", ["mobile"]);
  assert.equal(result.functionalStatus, "pass");
  assert.equal(result.visualStatus, "fail");
  assert.equal(result.visualIssueCount, 4);
  assert.equal(result.status, "fail");

  const aligned = structuredClone(mismatched);
  aligned.visual.analysis = { status: "passed", issueCount: 0 };
  const good = summarizeFixtureReport(aligned, "job-queue", ["mobile"]);
  assert.equal(good.status, "pass");
  assert.equal(good.visualStatus, "pass");
  assert.equal(summarizeFixtureReport(sample, "job-queue", ["mobile"]).visualStatus, "skipped");
});

test("Research Inbox QA does not erase the prior matrix captures", async () => {
  const shell = await readFile(new URL("../../../scripts/cam-ui-profile-matrix.sh", import.meta.url), "utf8");
  assert.match(shell, /if ! CAM_UI_QA_KEEP="\$KEEP_RUNS" npm run ui:qa --/);
});

test("absent results and missing metric audits never count as completed matrix", () => {
  assert.equal(summarizeFixtureReport({ states: ["default"], results: [] }, "x", ["mobile"]).status, "fail");
  const missingAudit = structuredClone(sample);
  delete missingAudit.results[0].qualityAudit;
  assert.equal(summarizeFixtureReport(missingAudit, "x", ["mobile"]).status, "fail");
});
