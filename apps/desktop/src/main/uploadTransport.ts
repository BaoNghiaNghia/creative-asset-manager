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
  if (response.status === 401) throw new Error("auth_required");
  if (response.status === 413) throw new Error("file_too_large");
  if (response.status === 422) throw new Error("invalid_destination");
  if (response.status === 403) {
    const payload = await response.clone().json().catch(() => undefined) as
      | { detail?: string | { code?: string; message?: string } }
      | undefined;
    const detail = payload?.detail;
    const message = typeof detail === "string" ? detail : detail?.message || "";
    if (/write access|read\/write|reconnect.*drive/i.test(message)) {
      throw new Error("drive_write_required");
    }
    throw new Error("permission_denied");
  }
  throw new Error(`http_${response.status}`);
}
export function createUploadTransport(session: Session, pageUrl: () => string): UploadTransport {
  // Main-process uploads must explicitly reuse the authenticated Electron
  // session. Chromium also owns transfer framing, so do not set the restricted
  // Content-Length header manually for streamed request bodies.
  const request = (input: string, init: RequestInit) => session.fetch(input, {
    ...init,
    credentials: "include",
  });
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
          headers: { "content-type": item.mimeType },
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
