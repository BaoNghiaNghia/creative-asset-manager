import assert from "node:assert/strict";
import test from "node:test";
import { assessVisualReviewRun, buildVisualReviewRows, reportMatchesPlan } from "./ui-qa-visual-review.mjs";

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


test("visual review rejects incomplete runs rather than suggesting baseline approval", () => {
  const plan = { viewports: ["desktop", "mobile"], steps: [{name:"default"},{name:"stage4"}] };
  const row = viewport => ({
    viewport,
    screenshots: [viewport + "--default.png", viewport + "--stage4.png"],
    qualityAudit: [{state:"default"},{state:"stage4"}],
    issues: {consoleErrors:[],pageErrors:[],requestFailures:[],badResponses:[],stepFailures:[]},
  });
  const report = {states:["default","stage4"], results:[row("desktop"),row("mobile")]};
  assert.deepEqual(assessVisualReviewRun(report, plan),
    {readyForHumanReview:true,blockers:[]});
  const broken = structuredClone(report);
  broken.results[1].issues.stepFailures.push({state:"stage4",message:"Modal missing"});
  broken.results[1].screenshots.pop();
  const assessment = assessVisualReviewRun(broken, plan);
  assert.equal(assessment.readyForHumanReview, false);
  assert.ok(assessment.blockers.includes("stepFailures:mobile"));
  assert.ok(assessment.blockers.includes("incomplete-screenshots:mobile"));
  assert.ok(assessVisualReviewRun({...report,results:[row("desktop")]}, plan)
    .blockers.includes("missing-viewport:mobile"));
});

test("latest review selection excludes unrelated Research Inbox runs on the same URL", () => {
  const main = {steps:[{name:"stage0-actions"},{name:"stage5-actions"}]};
  assert.equal(reportMatchesPlan({states:["stage0-actions","stage5-actions"]},main),true);
  assert.equal(reportMatchesPlan({states:["stage0-products","research-inbox-open"]},main),false);
  assert.equal(reportMatchesPlan({states:["stage0-actions"]},main),false);
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
