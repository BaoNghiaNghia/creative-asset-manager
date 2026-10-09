import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

test("Browser QA writes failure report and screenshot even when interaction assertion fails", { timeout: 60_000 }, async () => {
  const temp = await mkdtemp(path.join(os.tmpdir(), "cam-ui-qa-failure-"));
  const server = createServer((request, response) => {
    response.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    response.end('<html><body><main id="ready">Ready, not completed</main></body></html>');
  });
  try {
    await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
    const port = server.address().port;
    const plan = path.join(temp, "plan.json");
    await writeFile(plan, JSON.stringify({ steps: [
      { name: "default", waitFor: "#ready" },
      { name: "wrong-state", assertions: [
        { type: "text-includes", selector: "#ready", value: "Completed" },
      ] },
    ] }));
    const script = fileURLToPath(new URL("./ui-qa.mjs", import.meta.url));
    const clientRoot = path.resolve(path.dirname(script), "..");
    const output = path.join(temp, "output");
    const child = spawn(process.execPath, [script,
      "--url", `http://127.0.0.1:${port}/`,
      "--plan", plan,
      "--output", output,
      "--viewports", "desktop",
      "--strict",
    ], { cwd: clientRoot, env: {
      ...process.env, CAM_UI_QA_SETTLE_MS: "0", CAM_UI_QA_STEP_SETTLE_MS: "0",
    } });
    let stderr = "";
    child.stderr.on("data", data => { stderr += data.toString(); });
    const exitCode = await new Promise((resolve, reject) => {
      child.on("error", reject);
      child.on("close", resolve);
    });
    assert.equal(exitCode, 2, stderr);
    const [runName] = await readdir(output);
    const runRoot = path.join(output, runName);
    const report = JSON.parse(await readFile(path.join(runRoot, "report.json"), "utf8"));
    assert.equal(report.results[0].issues.stepFailures.length, 1);
    assert.equal(report.results[0].issues.stepFailures[0].state, "wrong-state");
    assert.ok(report.results[0].screenshots.includes("desktop--wrong-state--failure.png"));
    await readFile(path.join(runRoot, "desktop--wrong-state--failure.png"));
  } finally {
    await new Promise(resolve => server.close(resolve));
    await rm(temp, { recursive: true, force: true });
  }
});
