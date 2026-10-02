import fs from "node:fs/promises";
import path from "node:path";

async function isQaRunDirectory(directory) {
  try {
    const stat = await fs.stat(path.join(directory, "report.json"));
    return stat.isFile();
  } catch {
    return false;
  }
}

export async function cleanupRuns(outputRoot, keep) {
  await fs.mkdir(outputRoot, { recursive: true });
  const entries = await fs.readdir(outputRoot, { withFileTypes: true });
  const dirs = [];
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const fullPath = path.join(outputRoot, entry.name);
    if (!(await isQaRunDirectory(fullPath))) continue;
    const stat = await fs.stat(fullPath);
    dirs.push({ fullPath, mtimeMs: stat.mtimeMs });
  }
  dirs.sort((a, b) => b.mtimeMs - a.mtimeMs);
  await Promise.all(
    dirs.slice(keep).map((entry) =>
      fs.rm(entry.fullPath, { recursive: true, force: true }),
    ),
  );
}
