import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { IngestionService, type UploadTransport } from "./ingestion";

const temporaryDirectories: string[] = [];

afterEach(async () => {
  await Promise.all(temporaryDirectories.splice(0).map(path => rm(path, { recursive: true, force: true })));
});

async function waitFor(
  read: () => ReturnType<IngestionService["snapshot"]>,
  predicate: (value: ReturnType<IngestionService["snapshot"]>) => boolean,
) {
  const deadline = Date.now() + 3000;
  for (;;) {
    const value = read();
    if (predicate(value)) return value;
    if (Date.now() >= deadline) throw new Error("Timed out waiting for ingestion state");
    await new Promise(resolve => setTimeout(resolve, 20));
  }
}

async function sampleFile(name = "sample.png") {
  const directory = await mkdtemp(join(tmpdir(), "cam-ingestion-"));
  temporaryDirectories.push(directory);
  const absolutePath = join(directory, name);
  await writeFile(absolutePath, Buffer.from([1, 2, 3, 4, 5]));
  return absolutePath;
}

describe("desktop ingestion", () => {
  it("returns an initial job immediately and continues when dedupe preflight is unavailable", async () => {
    const uploaded: string[] = [];
    const transport: UploadTransport = {
      preflight: async () => { throw new Error("http_503"); },
      upload: async (item, _signal, progress) => {
        progress(item.size);
        uploaded.push(item.filename);
      },
    };
    const service = new IngestionService(transport, () => undefined, 1);
    const file = await sampleFile();

    const initial = await service.ingestRoots(
      [file],
      { parentId: "folder-1", provider: "google-drive", externalSourceId: "source-1" },
    );

    expect(initial.status).toBe("scanning");
    const completed = await waitFor(
      () => service.snapshot(initial.id),
      value => value.status === "completed",
    );
    expect(completed.completed).toBe(1);
    expect(uploaded).toEqual(["sample.png"]);
  });

  it("keeps a failed upload visible with a safe error code", async () => {
    const transport: UploadTransport = {
      preflight: async () => new Set(),
      upload: async () => { throw new Error("drive_write_required"); },
    };
    const service = new IngestionService(transport, () => undefined, 1);
    const file = await sampleFile("denied.png");

    const initial = await service.ingestRoots(
      [file],
      { parentId: "folder-1", provider: "google-drive" },
    );
    const failed = await waitFor(
      () => service.snapshot(initial.id),
      value => value.status === "failed",
    );

    expect(failed.failed).toBe(1);
    expect(failed.items[0]?.errorCode).toBe("drive_write_required");
  });
});
