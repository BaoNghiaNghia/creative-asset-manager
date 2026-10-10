/**
 * One-time interactive capture of a dedicated QA account session.
 *
 * Run on a trusted desktop with a GUI, never on the root-only production VPS.
 * This writes cookies and local storage, so output must stay outside the repo.
 */
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { firefox } from "playwright";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");

function argValue(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : undefined;
}

export function validateCaptureTarget(urlValue, outputValue) {
  const url = new URL(urlValue);
  if (url.protocol !== "https:" || url.username || url.password) {
    throw new Error("Session capture requires an HTTPS URL without embedded credentials.");
  }
  if (!outputValue) throw new Error("Provide --output outside the repository.");
  const output = path.resolve(outputValue);
  const relative = path.relative(repoRoot, output);
  if (!relative.startsWith("..") && !path.isAbsolute(relative)) {
    throw new Error("QA session credentials must be stored outside the repository.");
  }
  return { url, output };
}

async function main() {
  const { url, output } = validateCaptureTarget(
    argValue("--url") || "https://creative-assets.ddns.net",
    argValue("--output"),
  );
  if (!process.stdin.isTTY) {
    throw new Error("Run this capture interactively on a trusted desktop.");
  }
  try {
    await fs.access(output);
    throw new Error("Output already exists. Choose a new path; do not overwrite a live session.");
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  await fs.mkdir(path.dirname(output), { recursive: true });
  const browser = await firefox.launch({ headless: false });
  let temporary = null;
  try {
    const context = await browser.newContext();
    const page = await context.newPage();
    await page.goto(new URL("/ai-operations", url).href, {
      waitUntil: "domcontentloaded",
      timeout: 30_000,
    });
    console.log("Sign in with the dedicated QA account in the Firefox window.");
    console.log("Session is saved only after the AI Operations workspace is visible.");
    await page.locator(".ops-content").waitFor({ state: "visible", timeout: 15 * 60 * 1_000 });
    const state = await context.storageState();
    if (!state.cookies.length && !state.origins.length) {
      throw new Error("Authentication storage is empty; session was not saved.");
    }
    temporary = output + ".partial-" + process.pid;
    const handle = await fs.open(temporary, "wx", 0o600);
    try {
      await handle.writeFile(JSON.stringify(state) + "\n", "utf8");
    } finally {
      await handle.close();
    }
    await fs.chmod(temporary, 0o600);
    await fs.rename(temporary, output);
    temporary = null;
    console.log("QA session saved to the protected output path.");
    console.log("Treat it as a password; transfer securely to the VPS, mode 600.");
  } finally {
    if (temporary) await fs.rm(temporary, { force: true });
    await browser.close();
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await main();
}
