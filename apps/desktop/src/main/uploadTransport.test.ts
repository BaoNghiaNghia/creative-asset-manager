import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { Session } from "electron";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createUploadTransport } from "./uploadTransport";

const temporaryDirectories: string[] = [];

afterEach(async () => {
  await Promise.all(temporaryDirectories.splice(0).map(path => rm(path, { recursive: true, force: true })));
});

function sessionWith(fetchImpl: (input: string, init?: RequestInit) => Promise<Response>): Session {
  return { fetch: vi.fn(fetchImpl) } as unknown as Session;
}

describe("desktop upload transport", () => {
  it("reuses the authenticated Electron session for dedupe preflight", async () => {
    const session = sessionWith(async (_input, init) => {
      expect(init?.credentials).toBe("include");
      return new Response(JSON.stringify({ existing: {} }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });
    const transport = createUploadTransport(session, () => "https://creative-assets.ddns.net/");

    await expect(transport.preflight(["a".repeat(64)])).resolves.toEqual(new Set());
  });

  it("streams upload bytes without setting Chromium's restricted Content-Length header", async () => {
    const directory = await mkdtemp(join(tmpdir(), "cam-upload-"));
    temporaryDirectories.push(directory);
    const absolutePath = join(directory, "sample.txt");
    await writeFile(absolutePath, "hello");

    const session = sessionWith(async (input, init) => {
      const headers = new Headers(init?.headers);
      expect(headers.has("content-length")).toBe(false);
      expect(headers.get("content-type")).toBe("text/plain");
      expect(init?.credentials).toBe("include");
      expect(input).toContain("/api/explorer/upload?");
      expect(input).toContain("filename=sample.txt");

      const stream = init?.body as ReadableStream<Uint8Array>;
      const reader = stream.getReader();
      let uploaded = 0;
      for (;;) {
        const part = await reader.read();
        if (part.done) break;
        uploaded += part.value.byteLength;
      }
      expect(uploaded).toBe(5);
      return new Response("{}", { status: 200 });
    });

    const transport = createUploadTransport(session, () => "https://creative-assets.ddns.net/");
    const progress: number[] = [];
    await transport.upload({
      absolutePath,
      filename: "sample.txt",
      mimeType: "text/plain",
      size: 5,
      destination: { parentId: "folder-1", provider: "google-drive", externalSourceId: "source-1" },
    }, new AbortController().signal, bytes => progress.push(bytes));

    expect(progress.at(-1)).toBe(5);
  });
});
