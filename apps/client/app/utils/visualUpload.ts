export const VISUAL_UPLOAD_SERVER_LIMIT_BYTES = 25_000_000;
export const VISUAL_UPLOAD_TARGET_BYTES = 8_000_000;
export const VISUAL_UPLOAD_MAX_EDGE = 4096;
export const VISUAL_UPLOAD_MAX_PIXELS = 16_000_000;

export type VisualUploadPreparation = {
  file: File;
  optimized: boolean;
  originalBytes: number;
  outputBytes: number;
  width: number | null;
  height: number | null;
};

type DecodedVisualUpload = {
  source: CanvasImageSource;
  width: number;
  height: number;
  dispose: () => void;
};

export type VisualUploadRuntime = {
  decode: (file: File) => Promise<DecodedVisualUpload>;
  encode: (
    source: CanvasImageSource,
    width: number,
    height: number,
    mimeType: string,
    quality: number,
  ) => Promise<Blob>;
};

export function shouldOptimizeVisualUpload(fileSize: number, width: number, height: number): boolean {
  return fileSize > VISUAL_UPLOAD_TARGET_BYTES
    || Math.max(width, height) > VISUAL_UPLOAD_MAX_EDGE
    || width * height > VISUAL_UPLOAD_MAX_PIXELS;
}

export function visualUploadTargetDimensions(width: number, height: number): { width: number; height: number } {
  const edgeScale = Math.min(1, VISUAL_UPLOAD_MAX_EDGE / Math.max(width, height));
  const pixelScale = Math.min(1, Math.sqrt(VISUAL_UPLOAD_MAX_PIXELS / Math.max(1, width * height)));
  const scale = Math.min(edgeScale, pixelScale);
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  };
}

function defaultRuntime(): VisualUploadRuntime {
  return {
    decode(file) {
      return new Promise((resolve, reject) => {
        const url = URL.createObjectURL(file);
        const image = new Image();
        image.decoding = "async";
        image.onload = () => resolve({
          source: image,
          width: image.naturalWidth,
          height: image.naturalHeight,
          dispose: () => URL.revokeObjectURL(url),
        });
        image.onerror = () => {
          URL.revokeObjectURL(url);
          reject(new Error("This image format cannot be optimized in this browser."));
        };
        image.src = url;
      });
    },
    encode(source, width, height, mimeType, quality) {
      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      const context = canvas.getContext("2d", { alpha: true });
      if (!context) return Promise.reject(new Error("Image optimization is unavailable in this browser."));
      context.imageSmoothingEnabled = true;
      context.imageSmoothingQuality = "high";
      context.drawImage(source, 0, 0, width, height);
      return new Promise((resolve, reject) => canvas.toBlob(
        blob => blob ? resolve(blob) : reject(new Error("Image optimization failed.")),
        mimeType,
        quality,
      ));
    },
  };
}

function asPreparedFile(original: File, blob: Blob): File {
  return new File([blob], original.name, {
    type: blob.type || "image/webp",
    lastModified: original.lastModified,
  });
}

export async function prepareVisualSearchUpload(
  file: File,
  runtime: VisualUploadRuntime = defaultRuntime(),
): Promise<VisualUploadPreparation> {
  let decoded: DecodedVisualUpload;
  try {
    decoded = await runtime.decode(file);
  } catch (error) {
    if (file.size <= VISUAL_UPLOAD_SERVER_LIMIT_BYTES) {
      return {
        file,
        optimized: false,
        originalBytes: file.size,
        outputBytes: file.size,
        width: null,
        height: null,
      };
    }
    throw new Error(
      "This image is too large to search directly and could not be optimized in this browser. Try JPG, PNG, or WebP.",
      { cause: error },
    );
  }

  try {
    const { width, height } = decoded;
    if (!shouldOptimizeVisualUpload(file.size, width, height)) {
      return {
        file,
        optimized: false,
        originalBytes: file.size,
        outputBytes: file.size,
        width,
        height,
      };
    }

    let dimensions = visualUploadTargetDimensions(width, height);
    let blob: Blob | null = null;
    const qualities = [0.9, 0.82, 0.74, 0.66];

    for (const quality of qualities) {
      blob = await runtime.encode(decoded.source, dimensions.width, dimensions.height, "image/webp", quality);
      if (blob.size <= VISUAL_UPLOAD_TARGET_BYTES) break;
    }

    // Highly detailed PNG/TIFF artwork can still be large after quality reduction.
    // Shrink the pixel budget gradually while preserving aspect ratio.
    for (let attempt = 0; blob && blob.size > VISUAL_UPLOAD_TARGET_BYTES && attempt < 3; attempt += 1) {
      const ratio = Math.max(0.55, Math.min(0.9, Math.sqrt(VISUAL_UPLOAD_TARGET_BYTES / blob.size) * 0.94));
      dimensions = {
        width: Math.max(1, Math.round(dimensions.width * ratio)),
        height: Math.max(1, Math.round(dimensions.height * ratio)),
      };
      blob = await runtime.encode(decoded.source, dimensions.width, dimensions.height, "image/webp", 0.78);
    }

    if (!blob || blob.size > VISUAL_UPLOAD_SERVER_LIMIT_BYTES) {
      throw new Error("This image is still too large after optimization.");
    }

    // Do not replace a small-enough original with a larger encoded copy.
    if (
      blob.size >= file.size
      && file.size <= VISUAL_UPLOAD_SERVER_LIMIT_BYTES
      && Math.max(width, height) <= 20_000
      && width * height <= 120_000_000
    ) {
      return {
        file,
        optimized: false,
        originalBytes: file.size,
        outputBytes: file.size,
        width,
        height,
      };
    }

    const preparedFile = asPreparedFile(file, blob);
    return {
      file: preparedFile,
      optimized: true,
      originalBytes: file.size,
      outputBytes: preparedFile.size,
      width: dimensions.width,
      height: dimensions.height,
    };
  } finally {
    decoded.dispose();
  }
}
