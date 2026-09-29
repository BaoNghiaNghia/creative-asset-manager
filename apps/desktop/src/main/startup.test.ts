import { mkdtemp, mkdir, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  prepareTransientCachesOnVersionChange,
  shouldRefreshTransientCaches,
  shouldRetryMainFrameLoad,
  startupRetryDelayMs,
} from "./startup";

const roots: string[] = [];

afterEach(async () => {
  await Promise.all(roots.splice(0).map(path => rm(path, { recursive: true, force: true })));
});

describe("desktop startup recovery", () => {
  it("uses bounded retry timing and ignores expected aborted navigations", () => {
    expect(startupRetryDelayMs(0)).toBe(750);
    expect(startupRetryDelayMs(1)).toBe(1500);
    expect(startupRetryDelayMs(9)).toBe(4000);
    expect(shouldRetryMainFrameLoad(true, -105, 0)).toBe(true);
    expect(shouldRetryMainFrameLoad(true, -3, 0)).toBe(false);
    expect(shouldRetryMainFrameLoad(false, -105, 0)).toBe(false);
    expect(shouldRetryMainFrameLoad(true, -105, 2)).toBe(false);
  });

  it("refreshes only transient caches once per desktop version", async () => {
    const root = await mkdtemp(join(tmpdir(), "cam-startup-"));
    roots.push(root);
    await mkdir(join(root, "GPUCache"), { recursive: true });

    const clearHttpCache = vi.fn(async () => undefined);
    const clearCodeCaches = vi.fn(async () => undefined);

    expect(shouldRefreshTransientCaches(undefined, "0.1.8")).toBe(true);
    const first = await prepareTransientCachesOnVersionChange({
      userDataPath: root,
      currentVersion: "0.1.8",
      clearHttpCache,
      clearCodeCaches,
    });

    expect(first).toBe(true);
    expect(clearHttpCache).toHaveBeenCalledTimes(1);
    expect(clearCodeCaches).toHaveBeenCalledTimes(1);
    expect((await readFile(join(root, ".cam-desktop-version"), "utf8")).trim()).toBe("0.1.8");

    const second = await prepareTransientCachesOnVersionChange({
      userDataPath: root,
      currentVersion: "0.1.8",
      clearHttpCache,
      clearCodeCaches,
    });

    expect(second).toBe(false);
    expect(clearHttpCache).toHaveBeenCalledTimes(1);
    expect(clearCodeCaches).toHaveBeenCalledTimes(1);
  });
});
