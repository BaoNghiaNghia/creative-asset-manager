// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import {
  prepareVisualSearchUpload,
  shouldOptimizeVisualUpload,
  visualUploadTargetDimensions,
  VISUAL_UPLOAD_MAX_EDGE,
  VISUAL_UPLOAD_SERVER_LIMIT_BYTES,
  VISUAL_UPLOAD_TARGET_BYTES,
  type VisualUploadRuntime,
} from "./visualUpload";

function sizedFile(size: number, type = "image/png") {
  const file = new File(["x"], "large-art.png", { type, lastModified: 123 });
  Object.defineProperty(file, "size", { value: size });
  return file;
}

function sizedBlob(size: number, type = "image/webp") {
  const blob = new Blob(["x"], { type });
  Object.defineProperty(blob, "size", { value: size });
  return blob;
}

function runtime(width: number, height: number, outputSizes: number[]): VisualUploadRuntime {
  let index = 0;
  return {
    decode: vi.fn(async () => ({
      source: {} as CanvasImageSource,
      width,
      height,
      dispose: vi.fn(),
    })),
    encode: vi.fn(async () => sizedBlob(outputSizes[Math.min(index++, outputSizes.length - 1)])),
  };
}

describe("visual search upload optimization", () => {
  it("keeps normal search images untouched", async () => {
    const file = sizedFile(2_000_000);
    const mockRuntime = runtime(1600, 1200, [1_000_000]);
    const result = await prepareVisualSearchUpload(file, mockRuntime);
    expect(result.file).toBe(file);
    expect(result.optimized).toBe(false);
    expect(mockRuntime.encode).not.toHaveBeenCalled();
  });

  it("optimizes files before they reach the backend byte limit", async () => {
    const file = sizedFile(34_000_000);
    const mockRuntime = runtime(7000, 5000, [12_000_000, 7_000_000]);
    const result = await prepareVisualSearchUpload(file, mockRuntime);
    expect(result.optimized).toBe(true);
    expect(result.outputBytes).toBeLessThanOrEqual(VISUAL_UPLOAD_TARGET_BYTES);
    expect(result.width).toBeLessThanOrEqual(VISUAL_UPLOAD_MAX_EDGE);
    expect(result.height).toBeLessThanOrEqual(VISUAL_UPLOAD_MAX_EDGE);
    expect(mockRuntime.encode).toHaveBeenCalledTimes(2);
  });

  it("shrinks dimensions again when detailed artwork remains too large", async () => {
    const file = sizedFile(30_000_000);
    const mockRuntime = runtime(9000, 9000, [
      15_000_000, 14_000_000, 13_000_000, 12_000_000,
      10_000_000, 6_000_000,
    ]);
    const result = await prepareVisualSearchUpload(file, mockRuntime);
    expect(result.optimized).toBe(true);
    expect(result.outputBytes).toBeLessThanOrEqual(VISUAL_UPLOAD_TARGET_BYTES);
    expect((mockRuntime.encode as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(4);
    expect((result.width || 0) / (result.height || 1)).toBeCloseTo(1, 2);
  });

  it("falls back to the original when the browser cannot decode a server-safe image", async () => {
    const file = sizedFile(5_000_000, "image/heic");
    const mockRuntime: VisualUploadRuntime = {
      decode: vi.fn(async () => { throw new Error("unsupported"); }),
      encode: vi.fn(),
    };
    const result = await prepareVisualSearchUpload(file, mockRuntime);
    expect(result.file).toBe(file);
    expect(result.optimized).toBe(false);
  });

  it("shows a controlled error when an oversized image cannot be browser-optimized", async () => {
    const file = sizedFile(VISUAL_UPLOAD_SERVER_LIMIT_BYTES + 1, "image/heic");
    const mockRuntime: VisualUploadRuntime = {
      decode: vi.fn(async () => { throw new Error("unsupported"); }),
      encode: vi.fn(),
    };
    await expect(prepareVisualSearchUpload(file, mockRuntime)).rejects.toThrow("too large to search directly");
  });

  it("caps both long edge and pixel budget while preserving aspect ratio", () => {
    expect(shouldOptimizeVisualUpload(1_000_000, 12_000, 2_000)).toBe(true);
    const landscape = visualUploadTargetDimensions(12_000, 2_000);
    expect(landscape.width).toBe(VISUAL_UPLOAD_MAX_EDGE);
    expect(landscape.height).toBeCloseTo(683, 0);
    const square = visualUploadTargetDimensions(10_000, 10_000);
    expect(square.width * square.height).toBeLessThanOrEqual(16_100_000);
  });
});
