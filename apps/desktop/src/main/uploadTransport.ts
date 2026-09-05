import { createReadStream } from "node:fs";
import { Readable } from "node:stream";
import type { Session } from "electron";
import type { Destination, UploadTransport } from "./ingestion";

function appOrigin(value: string): string {
  const url = new URL(value);
  if (!["https:", "http:"].includes(url.protocol)) throw new Error("network");
  return url.origin;
}
async function responseError(response: Response): Promise<never> {
  if (response.status === 429 || response.status >= 500) throw new Error(`http_${response.status}`);
  throw new Error(`http_${response.status}`);
}
export function createUploadTransport(session: Session, pageUrl: () => string): UploadTransport {
  const request = (input: string, init: RequestInit) => session.fetch(input, init);
  return {
    async preflight(hashes) {
      const response = await request(new URL("/api/explorer/upload/dedupe-preflight", appOrigin(pageUrl())).toString(), {
        method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ hashes }),
      });
      if (!response.ok) await responseError(response);
      const data = await response.json() as { existing?: Record<string, boolean> };
      return new Set(Object.entries(data.existing || {}).filter(([, exists]) => exists).map(([hash]) => hash));
    },
    async upload(item, signal, progress) {
      const url = new URL("/api/explorer/upload", appOrigin(pageUrl()));
      url.searchParams.set("parent_id", item.destination.parentId);
      url.searchParams.set("provider", item.destination.provider);
      if (item.destination.externalSourceId) url.searchParams.set("external_source_id", item.destination.externalSourceId);
      url.searchParams.set("filename", item.filename);
      url.searchParams.set("mime_type", item.mimeType);
      let uploaded = 0;
      const source = createReadStream(item.absolutePath);
      signal.addEventListener("abort", () => source.destroy(), { once: true });
      source.on("data", chunk => { uploaded += chunk.length; progress(uploaded); });
      try {
        const response = await request(url.toString(), {
          method: "POST",
          headers: { "content-type": item.mimeType, "content-length": String(item.size) },
          body: Readable.toWeb(source) as unknown as BodyInit,
          signal,
          duplex: "half",
        } as RequestInit);
        if (!response.ok) await responseError(response);
      } catch (error) {
        if (signal.aborted) throw error;
        if (error instanceof Error && /^http_/.test(error.message)) throw error;
        throw new Error("network");
      }
    },
  };
}
