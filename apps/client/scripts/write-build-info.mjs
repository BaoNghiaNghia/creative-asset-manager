import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const clientRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = resolve(clientRoot, "../..");
const packageJson = JSON.parse(
  readFileSync(resolve(clientRoot, "package.json"), "utf8"),
);

function safeBuildCommit() {
  const configured = process.env.BUILD_COMMIT?.trim();
  if (configured && /^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$/.test(configured)) {
    return configured;
  }
  try {
    return execFileSync("git", ["rev-parse", "--verify", "HEAD"], {
      cwd: repositoryRoot,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
  } catch {
    return "unknown";
  }
}

function safeBuildTimestamp() {
  const configured = process.env.BUILD_UTC_TIMESTAMP?.trim();
  if (!configured) return new Date().toISOString();

  const parsed = new Date(configured);
  if (!Number.isFinite(parsed.valueOf())) {
    throw new Error("BUILD_UTC_TIMESTAMP must be a valid ISO-8601 timestamp");
  }
  return parsed.toISOString();
}

const buildInfo = {
  build_commit: safeBuildCommit(),
  build_utc_timestamp: safeBuildTimestamp(),
  frontend_version: packageJson.version,
};

const output = resolve(clientRoot, "dist/build-info.json");
mkdirSync(dirname(output), { recursive: true });
writeFileSync(output, JSON.stringify(buildInfo, null, 2) + "\n", {
  encoding: "utf8",
  mode: 0o644,
});
