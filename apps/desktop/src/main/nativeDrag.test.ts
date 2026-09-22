import { mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
  NativeDragService,
  nativeDragInternals,
  type NativeDragAssetRequest,
} from "./nativeDrag";

const roots: string[] = [];

async function temporaryRoot(): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), "cam-native-drag-test-"));
  roots.push(root);
  return root;
}

afterEach(async () => {
  await Promise.all(roots.splice(0).map(root => rm(root, { recursive: true, force: true })));
});

const item: NativeDragAssetRequest = {
  id: "asset-1",
  name: "camera-original.CR3",
  mimeType: "image/x-canon-cr3",
  provider: "google-drive",
  externalSourceId: "source-1",
  modifiedAt: "2026-09-22T08:00:00Z",
  size: 5,
};

function originalBytes() {
  return new Uint8Array([0, 1, 2, 3, 255]);
}

describe("NativeDragService", () => {
  it("materializes exact original bytes, reports stats, and reauthorizes cache reuse", async () => {
    const calls: Array<{ input: string; init?: RequestInit }> = [];
    const session = {
      async fetch(input: string, init?: RequestInit) {
        calls.push({ input, init });
        if (input.includes("/access")) {
          return new Response(null, {
            status: 204,
            headers: { "x-cam-asset-version": "version-1" },
          });
        }
        if (input.includes("/thumbnail/")) return new Response("thumb", { status: 200 });
        return new Response(originalBytes(), {
          status: 200,
          headers: {
            "content-length": "5",
            "x-cam-asset-version": "version-1",
          },
        });
      },
    };
    const service = new NativeDragService(
      session,
      () => "https://cam.example.com/folder/abc",
      await temporaryRoot(),
    );

    const first = await service.prepare([item]);
    expect(first.files).toHaveLength(1);
    expect(await readFile(first.files[0])).toEqual(Buffer.from(originalBytes()));
    expect(first.files[0].endsWith("camera-original.CR3")).toBe(true);
    expect(first.cacheHits).toBe(0);
    expect(first.cacheMisses).toBe(1);
    expect(first.totalBytes).toBe(5);
    expect(first.downloadedBytes).toBe(5);

    const mediaRequests = calls.filter(
      call => call.input.includes("/media/") && !call.input.includes("/access"),
    );
    expect(mediaRequests).toHaveLength(1);
    expect(mediaRequests[0].init?.headers).toEqual({ "accept-encoding": "identity" });
    expect(calls.filter(call => call.input.includes("/access"))).toHaveLength(1);

    const second = await service.prepare([item]);
    expect(second.files).toEqual(first.files);
    expect(second.cacheHits).toBe(1);
    expect(second.cacheMisses).toBe(0);
    expect(second.downloadedBytes).toBe(0);
    expect(calls.filter(call => call.input.includes("/access"))).toHaveLength(2);
    expect(
      calls.filter(call => call.input.includes("/media/") && !call.input.includes("/access")),
    ).toHaveLength(1);
  });

  it("lets prewarm reuse cache without a redundant access probe", async () => {
    let accessCalls = 0;
    const service = new NativeDragService(
      {
        async fetch(input: string) {
          if (input.includes("/access")) {
            accessCalls += 1;
            return new Response(null, { status: 204 });
          }
          if (input.includes("/thumbnail/")) return new Response("thumb", { status: 200 });
          return new Response(originalBytes(), { status: 200 });
        },
      },
      () => "https://cam.example.com/",
      await temporaryRoot(),
    );

    await service.prepare([item], { revalidateBeforeUse: false });
    const warmed = await service.prepare([item], { revalidateBeforeUse: false });
    expect(warmed.cacheHits).toBe(1);
    expect(accessCalls).toBe(0);

    await service.prepare([item]);
    expect(accessCalls).toBe(1);
  });

  it("reauthorizes start when it joins an in-flight prewarm download", async () => {
    let originalCalls = 0;
    let accessCalls = 0;
    let releaseMedia: (() => void) | undefined;
    let signalMediaStarted: (() => void) | undefined;
    const mediaStarted = new Promise<void>(resolve => { signalMediaStarted = resolve; });
    const mediaRelease = new Promise<void>(resolve => { releaseMedia = resolve; });
    const service = new NativeDragService(
      {
        async fetch(input: string) {
          if (input.includes("/access")) {
            accessCalls += 1;
            return new Response(null, { status: 204 });
          }
          if (input.includes("/thumbnail/")) return new Response(null, { status: 404 });
          originalCalls += 1;
          signalMediaStarted?.();
          await mediaRelease;
          return new Response(originalBytes(), { status: 200 });
        },
      },
      () => "https://cam.example.com/",
      await temporaryRoot(),
    );

    const prewarm = service.prepare([item], { revalidateBeforeUse: false });
    await mediaStarted;
    const start = service.prepare([item]);
    releaseMedia?.();

    await Promise.all([prewarm, start]);
    expect(originalCalls).toBe(1);
    expect(accessCalls).toBe(1);
  });

  it("rejects a cached original when the current session no longer has access", async () => {
    let allowAccess = true;
    const service = new NativeDragService(
      {
        async fetch(input: string) {
          if (input.includes("/access")) {
            return new Response(null, { status: allowAccess ? 204 : 403 });
          }
          if (input.includes("/thumbnail/")) return new Response("thumb", { status: 200 });
          return new Response(originalBytes(), { status: 200 });
        },
      },
      () => "https://cam.example.com/",
      await temporaryRoot(),
    );

    const prepared = await service.prepare([item], { revalidateBeforeUse: false });
    allowAccess = false;
    await expect(service.prepare([item])).rejects.toThrow("native_drag_access_http_403");
    await expect(stat(prepared.files[0])).rejects.toThrow();
  });

  it("limits concurrent original downloads", async () => {
    let active = 0;
    let maxActive = 0;
    const service = new NativeDragService(
      {
        async fetch(input: string) {
          if (input.includes("/thumbnail/")) return new Response(null, { status: 404 });
          if (input.includes("/access")) return new Response(null, { status: 204 });
          active += 1;
          maxActive = Math.max(maxActive, active);
          await new Promise(resolve => setTimeout(resolve, 15));
          active -= 1;
          return new Response(new Uint8Array([1]), { status: 200 });
        },
      },
      () => "https://cam.example.com/",
      await temporaryRoot(),
      { downloadConcurrency: 2 },
    );
    const items = Array.from({ length: 6 }, (_, index): NativeDragAssetRequest => ({
      ...item,
      id: `asset-${index}`,
      name: `asset-${index}.bin`,
      modifiedAt: `2026-09-22T08:00:0${index}Z`,
      size: 1,
    }));

    const prepared = await service.prepare(items, { revalidateBeforeUse: false });
    expect(prepared.cacheMisses).toBe(6);
    expect(maxActive).toBe(2);
  });

  it("limits cached access checks for multi-file drags", async () => {
    let activeAccess = 0;
    let maxActiveAccess = 0;
    const service = new NativeDragService(
      {
        async fetch(input: string) {
          if (input.includes("/access")) {
            activeAccess += 1;
            maxActiveAccess = Math.max(maxActiveAccess, activeAccess);
            await new Promise(resolve => setTimeout(resolve, 12));
            activeAccess -= 1;
            return new Response(null, { status: 204 });
          }
          if (input.includes("/thumbnail/")) return new Response(null, { status: 404 });
          return new Response(new Uint8Array([1]), { status: 200 });
        },
      },
      () => "https://cam.example.com/",
      await temporaryRoot(),
      { accessConcurrency: 2 },
    );
    const items = Array.from({ length: 6 }, (_, index): NativeDragAssetRequest => ({
      ...item,
      id: "cached-" + index,
      name: "cached-" + index + ".bin",
      modifiedAt: "2026-09-22T08:01:0" + index + "Z",
      size: 1,
    }));

    await service.prepare(items, { revalidateBeforeUse: false });
    const prepared = await service.prepare(items);
    expect(prepared.cacheHits).toBe(6);
    expect(maxActiveAccess).toBe(2);
  });

  it("evicts least-recently-used unprotected originals when the cache exceeds its budget", async () => {
    let originalCalls = 0;
    const service = new NativeDragService(
      {
        async fetch(input: string) {
          if (input.includes("/thumbnail/")) return new Response(null, { status: 404 });
          if (input.includes("/access")) return new Response(null, { status: 204 });
          originalCalls += 1;
          return new Response(new Uint8Array([1, 2, 3, 4]), { status: 200 });
        },
      },
      () => "https://cam.example.com/",
      await temporaryRoot(),
      { cacheMaxBytes: 6, cleanupIntervalMs: 60_000, cacheEvictionGraceMs: 1 },
    );
    const firstItem = { ...item, id: "old", name: "old.bin", size: 4 };
    const secondItem = { ...item, id: "new", name: "new.bin", size: 4 };

    await service.prepare([firstItem], { revalidateBeforeUse: false });
    await new Promise(resolve => setTimeout(resolve, 5));
    await service.prepare([secondItem], { revalidateBeforeUse: false });
    const firstAgain = await service.prepare([firstItem], { revalidateBeforeUse: false });

    expect(firstAgain.cacheMisses).toBe(1);
    expect(originalCalls).toBe(3);
  });

  it("redownloads the original when the server-side asset version changes at the same size", async () => {
    let version = "version-1";
    let mediaCalls = 0;
    const service = new NativeDragService(
      {
        async fetch(input: string) {
          if (input.includes("/access")) {
            return new Response(null, {
              status: 204,
              headers: { "x-cam-asset-version": version },
            });
          }
          if (input.includes("/thumbnail/")) return new Response(null, { status: 404 });
          mediaCalls += 1;
          const bytes = version === "version-1"
            ? new Uint8Array([1, 1, 1, 1, 1])
            : new Uint8Array([2, 2, 2, 2, 2]);
          return new Response(bytes, {
            status: 200,
            headers: { "x-cam-asset-version": version },
          });
        },
      },
      () => "https://cam.example.com/",
      await temporaryRoot(),
    );

    const first = await service.prepare([item]);
    expect(await readFile(first.files[0])).toEqual(Buffer.from([1, 1, 1, 1, 1]));

    version = "version-2";
    const second = await service.prepare([item]);
    expect(second.cacheMisses).toBe(1);
    expect(mediaCalls).toBe(2);
    expect(await readFile(second.files[0])).toEqual(Buffer.from([2, 2, 2, 2, 2]));
  });

  it("does not reuse a cached file whose size no longer matches the known original", async () => {
    let originalCalls = 0;
    const service = new NativeDragService(
      {
        async fetch(input: string) {
          if (input.includes("/access")) return new Response(null, { status: 204 });
          if (input.includes("/thumbnail/")) return new Response("thumb", { status: 200 });
          originalCalls += 1;
          return new Response(originalBytes(), { status: 200 });
        },
      },
      () => "https://cam.example.com/",
      await temporaryRoot(),
    );
    const first = await service.prepare([item]);
    await writeFile(first.files[0], Buffer.from([1]));
    const second = await service.prepare([item]);
    expect(second.cacheHits).toBe(0);
    expect(originalCalls).toBe(2);
    expect(await readFile(second.files[0])).toEqual(Buffer.from(originalBytes()));
  });

  it("rejects a materialized file when provider bytes do not match the known original size", async () => {
    const service = new NativeDragService(
      {
        async fetch(input: string) {
          if (input.includes("/thumbnail/")) return new Response("thumb", { status: 200 });
          return new Response(new Uint8Array([1, 2, 3]), { status: 200 });
        },
      },
      () => "https://cam.example.com/",
      await temporaryRoot(),
    );
    await expect(service.prepare([item])).rejects.toThrow("native_drag_original_size_mismatch");
  });

  it("builds same-origin media, thumbnail, and access endpoints from asset identity", () => {
    const media = nativeDragInternals.assetEndpoint(
      "https://cam.example.com/app",
      item,
      "media",
    );
    const thumbnail = nativeDragInternals.assetEndpoint(
      "https://cam.example.com/app",
      item,
      "thumbnail",
    );
    const access = nativeDragInternals.assetEndpoint(
      "https://cam.example.com/app",
      item,
      "access",
    );
    expect(media).toBe(
      "https://cam.example.com/api/explorer/media/asset-1?provider=google-drive&external_source_id=source-1",
    );
    expect(thumbnail).toContain("/api/explorer/thumbnail/asset-1?");
    expect(access).toContain("/api/explorer/media/asset-1/access?");
  });

  it("sanitizes the filename but preserves the original extension", () => {
    expect(nativeDragInternals.safeFilename("folder\\bad:name?.PSD")).toBe("bad-name-.PSD");
    expect(nativeDragInternals.safeFilename("CON.jpg")).toBe("_CON.jpg");
  });

  it("uses both version hints and app origin in the cache identity", () => {
    const original = nativeDragInternals.cacheIdentity(item, "https://cam.example.com");
    const changedVersion = nativeDragInternals.cacheIdentity({
      ...item,
      modifiedAt: "2026-09-22T09:00:00Z",
    }, "https://cam.example.com");
    const changedOrigin = nativeDragInternals.cacheIdentity(item, "https://other.example.com");
    expect(changedVersion).not.toBe(original);
    expect(changedOrigin).not.toBe(original);
  });
});
