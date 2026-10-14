/** Operator-only, local headed login: export a production QA session without printing secrets. */
import fs from "node:fs/promises";
import path from "node:path";
import readline from "node:readline/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { inspectProductionQaStorageState } from "./production-ui-auth-state.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const arg = name => {
  const index = process.argv.indexOf(name);
  return index < 0 ? "" : (process.argv[index + 1] || "");
};

export function validateSessionCaptureOptions(url, destination, root = ROOT) {
  let target;
  try { target = new URL(url); } catch { throw Error("A valid production HTTPS --url is required."); }
  if (target.protocol !== "https:" || target.hostname !== "creative-assets.ddns.net" || target.port || target.username || target.password ||
      ["localhost", "127.0.0.1", "::1"].includes(target.hostname)) {
    throw Error("Capture requires a production HTTPS URL without embedded credentials.");
  }
  if (!destination) throw Error("Choose an explicit --output path outside the Git repository.");
  const output = path.resolve(destination);
  const relative = path.relative(path.resolve(root), output);
  if (relative === "" || relative === ".." || (!relative.startsWith(".." + path.sep) && !path.isAbsolute(relative))) {
    throw Error("Session storage must be outside the Git repository.");
  }
  return { origin: target.origin, output };
}

async function main() {
  if (!process.stdin.isTTY) {
    throw Error("Session capture requires an interactive operator terminal; it cannot run unattended.");
  }
  const { origin, output } = validateSessionCaptureOptions(arg("--url"), arg("--output"));
  const destDir = path.dirname(output);
  await fs.mkdir(destDir, { recursive: true, mode: 0o700 });
  const realDir = await fs.realpath(destDir);
  validateSessionCaptureOptions(origin, path.join(realDir, path.basename(output)));
  const ui = readline.createInterface({ input: process.stdin, output: process.stdout });
  let browser;
  try {
    browser = await chromium.launch({ headless: false });
    const context = await browser.newContext();
    const page = await context.newPage();
    await page.goto(new URL("/ai-operations", origin).href, { waitUntil: "domcontentloaded" });
    console.log("Sign in manually using the authorized read-only QA account.");
    console.log("Confirm the AI Operations workspace is visible and not an access-denied page.");
    await ui.question("After successful sign-in, press Enter to save a local session state (or Ctrl+C to cancel): ");
    const href = new URL(page.url());
    if (href.origin !== origin) throw Error("Browser is not on the expected production origin.");
    const body = (await page.locator("body").innerText()).slice(0, 20000);
    if (/sign in required|access denied|workspace is not available for your role/i.test(body)) {
      throw Error("The QA account does not appear authorized for the workspace.");
    }
    const state = await context.storageState();
    const inspection = inspectProductionQaStorageState(state, origin);
    if (!inspection.ready) {
      throw Error("Session check failed: " + inspection.reason);
    }
    // Exclusive create: never overwrite a newer session without deliberate operator action.
    let handle;
    try {
      handle = await fs.open(output, "wx", 0o600);
      await handle.writeFile(JSON.stringify(state));
    } catch (error) {
      if (handle) await fs.unlink(output).catch(() => undefined);
      throw error;
    } finally {
      await handle?.close();
    }
    await fs.chmod(output, 0o600);
    console.log("QA session saved outside Git. No credentials were printed.");
    if (process.platform === "win32") console.log("Windows: restrict this file to the operator account using NTFS ACLs before transferring.");
    else console.log("File permissions: owner only (0600).");
    console.log("Transfer only through a trusted encrypted channel to the server's protected QA state path.");
  } finally {
    await browser?.close();
    ui.close();
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => { console.error("Production QA session setup:", error.message); process.exitCode = 1; });
}
