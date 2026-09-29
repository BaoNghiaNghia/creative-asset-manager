import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { spawn } from "node:child_process";
import { shell } from "electron";

type ChromeLocalState = {
  profile?: {
    last_used?: unknown;
    last_active_profiles?: unknown;
  };
};

function safeProfileDirectory(value: unknown): string | undefined {
  if (typeof value !== "string" || !value || value.length > 128) return undefined;
  if (value.includes("/") || value.includes("\\") || value.includes("..")) return undefined;
  return /^[A-Za-z0-9 _.-]+$/.test(value) ? value : undefined;
}

export function preferredChromeProfile(state: unknown): string | undefined {
  if (!state || typeof state !== "object") return undefined;
  const profile = (state as ChromeLocalState).profile;
  if (!profile || typeof profile !== "object") return undefined;

  const active = Array.isArray(profile.last_active_profiles)
    ? profile.last_active_profiles.map(safeProfileDirectory).find(Boolean)
    : undefined;
  return active || safeProfileDirectory(profile.last_used);
}

function chromeExecutableCandidates(): string[] {
  if (process.platform !== "win32") return [];
  const roots = [
    process.env.LOCALAPPDATA,
    process.env.PROGRAMFILES,
    process.env["PROGRAMFILES(X86)"],
  ].filter((value): value is string => Boolean(value));
  return roots.map(root => join(root, "Google", "Chrome", "Application", "chrome.exe"));
}

async function currentChromeProfile(): Promise<string | undefined> {
  if (process.platform !== "win32" || !process.env.LOCALAPPDATA) return undefined;
  try {
    const localStatePath = join(process.env.LOCALAPPDATA, "Google", "Chrome", "User Data", "Local State");
    const parsed = JSON.parse(await readFile(localStatePath, "utf8")) as unknown;
    return preferredChromeProfile(parsed);
  } catch {
    return undefined;
  }
}

async function openInChromeProfile(target: string): Promise<boolean> {
  const profile = await currentChromeProfile();
  const executable = chromeExecutableCandidates().find(existsSync);
  if (!profile || !executable) return false;

  try {
    const child = spawn(executable, [
      `--profile-directory=${profile}`,
      "--new-tab",
      target,
    ], {
      detached: true,
      stdio: "ignore",
      windowsHide: true,
    });
    child.unref();
    return true;
  } catch {
    return false;
  }
}

export async function openOAuthUrl(target: string): Promise<void> {
  // On Windows, prefer Chrome's most recently active profile. Launching an
  // already-running profile routes the OAuth tab into that profile, preserving
  // the Google/Microsoft account the user is currently using.
  if (await openInChromeProfile(target)) return;
  await shell.openExternal(target);
}
