import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { cleanupRuns } from "./ui-qa-cleanup.mjs";

async function createRun(root, name, mtimeMs) {
  const directory = path.join(root, name);
  await fs.mkdir(directory, { recursive: true });
  await fs.writeFile(path.join(directory, "report.json"), "{}\n");
  const when = new Date(mtimeMs);
  await fs.utimes(directory, when, when);
  return directory;
}

test("cleanupRuns prunes only completed QA run directories", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "cam-ui-qa-cleanup-"));
  try {
    const control = path.join(root, "autofix-sessions");
    const proposals = path.join(root, "baseline-proposals");
    await fs.mkdir(control, { recursive: true });
    await fs.mkdir(proposals, { recursive: true });
    await fs.writeFile(path.join(control, "session.final-attempted"), "marker\n");
    await fs.writeFile(path.join(proposals, "proposal.json"), "{}\n");

    const oldRun = await createRun(root, "2026-10-01T00-00-00-000Z", 1_000);
    const newestRun = await createRun(root, "2026-10-02T00-00-00-000Z", 2_000);

    await cleanupRuns(root, 1);

    await assert.doesNotReject(fs.access(newestRun));
    await assert.rejects(fs.access(oldRun));
    await assert.doesNotReject(fs.access(control));
    await assert.doesNotReject(fs.access(proposals));
    await assert.doesNotReject(
      fs.access(path.join(control, "session.final-attempted")),
    );
  } finally {
    await fs.rm(root, { recursive: true, force: true });
  }
});

test("cleanupRuns preserves arbitrary non-run directories even when keep is small", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "cam-ui-qa-control-"));
  try {
    for (const name of [
      "autofix-sessions",
      "repair-sessions",
      "baseline-proposals",
      "production-smoke",
    ]) {
      await fs.mkdir(path.join(root, name), { recursive: true });
    }
    await cleanupRuns(root, 1);
    for (const name of [
      "autofix-sessions",
      "repair-sessions",
      "baseline-proposals",
      "production-smoke",
    ]) {
      await assert.doesNotReject(fs.access(path.join(root, name)));
    }
  } finally {
    await fs.rm(root, { recursive: true, force: true });
  }
});
