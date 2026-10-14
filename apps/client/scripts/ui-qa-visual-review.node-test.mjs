import assert from "node:assert/strict";
import test from "node:test";
import { assessVisualReviewRun, buildVisualReviewRows, createVisualReviewPacket, reportMatchesPlan } from "./ui-qa-visual-review.mjs";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";

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

test("review packet exposes safe viewport/state filters but never auto-approves screenshots", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "cam-visual-review-"));
  try {
    const planDir = path.join(root, "docs/operations");
    const baselineDir = path.join(root, "apps/client/visual-baselines/realistic-review-ugc");
    const runDir = path.join(root, "apps/client/.ui-qa", "2026-10-14-test");
    await Promise.all([fs.mkdir(planDir, {recursive:true}),
      fs.mkdir(baselineDir, {recursive:true}), fs.mkdir(runDir,{recursive:true})]);
    const plan = {viewports:["mobile"],steps:[{name:"default"},{name:"stage1"}]};
    await fs.writeFile(path.join(planDir,"ui-qa-realistic-review-ugc-plan.json"),JSON.stringify(plan));
    await fs.writeFile(path.join(baselineDir,"manifest.json"), JSON.stringify({states:["default"]}));
    await fs.writeFile(path.join(baselineDir,"mobile--default.png"), "approved");
    const screenshots = ["mobile--default.png","mobile--stage1.png"];
    for (const name of screenshots) await fs.writeFile(path.join(runDir,name), "capture");
    const report = {runId:"safe-test",browser:{engine:"chrome"},
      states:["default","stage1"], results:[{
        viewport:"mobile", screenshots, qualityAudit:[{state:"default"},{state:"stage1"}],
        issues:{consoleErrors:[],pageErrors:[],requestFailures:[],badResponses:[],stepFailures:[]},
      }]};
    await fs.writeFile(path.join(runDir,"report.json"),JSON.stringify(report));
    const result=await createVisualReviewPacket({profileName:"realistic-review-ugc",root,runDir});
    assert.equal(result.status,"review-only");
    assert.deepEqual(result.pendingStates,["stage1"]);
    const html=await fs.readFile(result.htmlPath,"utf8");
    assert.match(html,/id="review-approval"/);
    assert.match(html,/id="review-viewport"/);
    assert.match(html,/id="review-search"/);
    assert.match(html,/data-approval="pending"/);
    assert.match(html,/data-approval="approved"/);
    assert.match(html,/Filtering never approves a baseline/);
    assert.deepEqual(JSON.parse(await fs.readFile(path.join(baselineDir,"manifest.json"),"utf8")),
      {states:["default"]});
  } finally { await fs.rm(root,{recursive:true,force:true}); }
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
