/**
 * Import a Creative Asset Management session AFTER signing in manually through
 * ordinary (unautomated) Google Chrome, using a dedicated isolated QA profile.
 * OAuth authentication never occurs under Playwright or an automation browser.
 *
 * Start Chrome normally first, authenticate, then CLOSE ALL Chrome windows for
 * the dedicated profile before running this capture.
 */
import fs from "node:fs/promises";
import path from "node:path";
import { spawn } from "node:child_process";
import { setTimeout as pause } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { validateCaptureTarget } from "./production-ui-auth-capture.mjs";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");

function argValue(name) {
  const at = process.argv.indexOf(name);
  return at >= 0 ? process.argv[at + 1] : undefined;
}

/** Only the application cookies and local storage are exported, NEVER Google cookies. */
export function appOnlyStorageState(state, urlValue) {
  const { hostname, origin } = new URL(urlValue);
  return {
    cookies: (state.cookies || []).filter(cookie => {
      const domain = String(cookie.domain || "").replace(/^\./, "").toLowerCase();
      return domain === hostname.toLowerCase() || domain.endsWith("." + hostname.toLowerCase());
    }),
    origins: (state.origins || []).filter(value => value.origin === origin).map(value => ({
      origin: value.origin,
      localStorage: value.localStorage || [],
    })),
  };
}

export function validateQaProfile(profileValue) {
  if (!profileValue) throw new Error("Provide --profile-dir for a dedicated QA Chrome profile.");
  const profile = path.resolve(profileValue);
  const relative = path.relative(repoRoot, profile);
  if (!relative.startsWith("..") && !path.isAbsolute(relative)) {
    throw new Error("QA Chrome profile must remain outside the repository.");
  }
  const home = process.env.HOME || "";
  if (!home || profile === home || profile === path.resolve(home, ".config/google-chrome")
    || profile === path.resolve(home, ".config/chromium")) {
    throw new Error("Use a dedicated QA profile, not your everyday browser profile.");
  }
  return profile;
}

async function getDebugPort(profile, child, maxWaitMs = 20_000) {
  const infoFile = path.join(profile, "DevToolsActivePort");
  for (let n = 0; n < maxWaitMs / 200; n++) {
    if (child.exitCode !== null || child.signalCode !== null) {
      throw new Error("Chrome exited before enabling local debugging. Ensure the QA profile is closed.");
    }
    try {
      const [port] = (await fs.readFile(infoFile, "utf8")).trim().split(/\r?\n/);
      const value = Number(port);
      if (Number.isInteger(value) && value >= 1024 && value <= 65535) return value;
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
    await pause(200);
  }
  throw new Error("Chrome debugging was not ready. Close other Chrome windows using the QA profile.");
}

async function saveRestrictedOutput(output, state) {
  const file = output + ".partial-" + process.pid;
  let created = false;
  try {
    const handle = await fs.open(file, "wx", 0o600);
    created = true;
    try { await handle.writeFile(JSON.stringify(state) + "\n", "utf8"); }
    finally { await handle.close(); }
    await fs.chmod(file, 0o600);
    await fs.rename(file, output);
    created = false;
  } finally {
    if (created) await fs.rm(file, { force: true });
  }
}

async function main() {
  if (!process.stdin.isTTY) throw new Error("Run interactively on your trusted Ubuntu desktop.");
  if (process.getuid?.() === 0) throw new Error("Never capture an authenticated session as root.");
  const { url, output } = validateCaptureTarget(
    argValue("--url") || "https://creative-assets.ddns.net",
    argValue("--output"),
  );
  const profile = validateQaProfile(argValue("--profile-dir"));
  const chromeBin = argValue("--chrome") || "google-chrome";

  const info = await fs.lstat(profile).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()) {
    throw new Error("QA profile not found or is a symlink. First sign in with normal Chrome using --user-data-dir.");
  }
  if (await fs.access(path.join(profile, "DevToolsActivePort")).then(() => true).catch(() => false)) {
    throw new Error("The QA profile has an active or stale DevToolsActivePort. Close Chrome for this profile before capture.");
  }
  if (await fs.access(output).then(() => true).catch(() => false)) {
    throw new Error("Output already exists. Choose a new path; do not overwrite a live QA session.");
  }
  await fs.mkdir(path.dirname(output), { recursive: true });

  // This Chrome process is used ONLY AFTER normal-browser manual authentication.
  // Debugging binds to loopback with a random port and an isolated profile.
  const child = spawn(chromeBin, [
    "--user-data-dir=" + profile,
    "--remote-debugging-address=127.0.0.1",
    "--remote-debugging-port=0",
    "--no-first-run",
    "--new-window",
    new URL("/ai-operations", url).href,
  ], { stdio: "ignore" });
  const spawnError = new Promise((_, reject) => child.once("error", error =>
    reject(new Error("Could not start Google Chrome (" + chromeBin + "): " + error.message))));
  let browser;
  try {
    const port = await Promise.race([getDebugPort(profile, child), spawnError]);
    browser = await chromium.connectOverCDP("http://127.0.0.1:" + port, { timeout: 15_000 });
    const context = browser.contexts()[0];
    if (!context) throw new Error("Could not find the regular Chrome browser context.");
    let page = context.pages().find(item => item.url().startsWith(url.origin));
    if (!page) page = await context.newPage();
    await page.goto(new URL("/ai-operations", url).href, {
      waitUntil: "domcontentloaded",
      timeout: 30_000,
    });
    try {
      await page.locator(".ops-content").waitFor({ state: "visible", timeout: 15_000 });
    } catch {
      throw new Error("The QA profile is not signed in to Creative Asset Management. Sign in with normal Chrome first; close it; retry capture.");
    }
    if (new URL(page.url()).origin !== url.origin) {
      throw new Error("Application redirected away from its origin; session was not saved.");
    }
    const filtered = appOnlyStorageState(await context.storageState(), url.href);
    if (!filtered.cookies.length && !filtered.origins.some(item => item.localStorage.length)) {
      throw new Error("No Creative Asset Management cookies or local storage found; refusing to save an empty session.");
    }
    await saveRestrictedOutput(output, filtered);
    console.log("Application-only QA state saved to: " + output);
    console.log("File permissions: 600. Google cookies were NOT exported.");
    console.log("Transfer privately to the VPS; never commit, upload to chat or expose this file.");
  } finally {
    await browser?.close().catch(() => {});
    if (child.exitCode === null) child.kill("SIGTERM");
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await main();
}
