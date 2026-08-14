export const TEXT_PREVIEW_MAX_BYTES = 1024 * 1024;
export const TEXT_PREVIEW_RANGE = "bytes=0-" + (TEXT_PREVIEW_MAX_BYTES - 1);

export async function readTextPreview(response: Response, signal: AbortSignal): Promise<{ text: string; truncated: boolean }> {
  if (!response.ok || !response.body) throw Error("text_preview_unavailable");
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  let truncated = response.status === 206 || Boolean(response.headers.get("content-range")?.match(/\/\d+$/));
  try {
    while (size < TEXT_PREVIEW_MAX_BYTES) {
      const next = await reader.read();
      if (next.done) break;
      const remaining = TEXT_PREVIEW_MAX_BYTES - size;
      if (next.value.byteLength > remaining) {
        chunks.push(next.value.slice(0, remaining));
        size += remaining;
        truncated = true;
        break;
      }
      chunks.push(next.value);
      size += next.value.byteLength;
    }
    if (size >= TEXT_PREVIEW_MAX_BYTES) truncated = true;
  } finally {
    if (truncated || signal.aborted) await reader.cancel().catch(() => undefined);
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  chunks.forEach(chunk => { bytes.set(chunk, offset); offset += chunk.byteLength; });
  return { text: new TextDecoder("utf-8", { fatal: false }).decode(bytes), truncated };
}
