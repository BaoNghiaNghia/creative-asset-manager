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

const marker = {
  build_commit: safeBuildCommit(),
  build_utc_timestamp: new Date().toISOString(),
  frontend_version: packageJson.version,
};

const output = resolve(clientRoot, "dist/build-meta.json");
mkdirSync(dirname(output), { recursive: true });
writeFileSync(output, JSON.stringify(marker, null, 2) + "\n", {
  encoding: "utf8",
  mode: 0o644,
});
