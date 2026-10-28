import { RrugcApiError } from "../api";
import type {
  TypographyDna, TypographyReference, TypographyReferenceFilters, TypographyReferencePage,
  TypographyReferencePatch, TypographyUploadBatch, TypographyRights, TypographyPrivacy,
} from "./types";

const BASE = "/api/v1/realistic-review-ugc/typography/references";

async function jsonRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const multipart = typeof FormData !== "undefined" && init?.body instanceof FormData;
  const response = await fetch(path, {
    credentials: "include", ...init,
    headers: { ...(multipart ? {} : { "Content-Type": "application/json" }), ...init?.headers },
  });
  if (!response.ok) {
    let code: string | undefined;
    let message = `Request failed (HTTP ${response.status}).`;
    try {
      const payload = await response.json();
      const detail = payload?.detail;
      if (typeof detail === "string") message = detail;
      else if (detail && typeof detail === "object") {
        if (typeof detail.code === "string") code = detail.code;
        if (typeof detail.message === "string") message = detail.message;
        else if (Array.isArray(detail)) {
          message = detail.filter(item => typeof item?.msg === "string")
            .slice(0, 3).map(item => item.msg).join("; ") || message;
        }
      }
    } catch { /* Reverse proxies sometimes return HTML. */ }
    throw new RrugcApiError(response.status, message, code);
  }
  return response.json() as Promise<T>;
}

export function listTypographyReferences(
  filters: TypographyReferenceFilters = {}, signal?: AbortSignal,
): Promise<TypographyReferencePage> {
  const query = new URLSearchParams({
    limit: String(filters.limit ?? 40),
    offset: String(filters.offset ?? 0),
    sort: filters.sort ?? "newest",
  });
  if (filters.query?.trim()) query.set("query", filters.query.trim());
  if (filters.status) query.set("status", filters.status);
  if (filters.sourceType) query.set("source_type", filters.sourceType);
  if (filters.scope) query.set("scope", filters.scope);
  if (filters.styleRole) query.set("style_role", filters.styleRole);
  if (filters.myUploadsOnly) query.set("my_uploads_only", "true");
  return jsonRequest(BASE + "?" + query.toString(), { signal });
}

export function getTypographyReference(id: string, signal?: AbortSignal): Promise<TypographyReference> {
  return jsonRequest(BASE + "/" + encodeURIComponent(id), { signal });
}

export function listTypographyDna(id: string, signal?: AbortSignal): Promise<{ items: TypographyDna[] }> {
  return jsonRequest(BASE + "/" + encodeURIComponent(id) + "/dna", { signal });
}

export function uploadTypographyReferences(
  files: File[], options: { rights: TypographyRights; privacy: TypographyPrivacy; sourceAttribution: string },
  signal?: AbortSignal,
): Promise<TypographyUploadBatch> {
  const form = new FormData();
  files.forEach(file => form.append("files", file, file.name));
  form.set("rights", options.rights);
  form.set("privacy", options.privacy);
  if (options.sourceAttribution.trim()) form.set("source_attribution", options.sourceAttribution.trim());
  return jsonRequest(BASE + "/uploads", { method: "POST", body: form, signal });
}

export function patchTypographyReference(id: string, changes: TypographyReferencePatch): Promise<TypographyReference> {
  return jsonRequest(BASE + "/" + encodeURIComponent(id), {
    method: "PATCH", body: JSON.stringify(changes),
  });
}

export function archiveTypographyReference(id: string): Promise<{ id: string; editorial_status: "archived" }> {
  return jsonRequest(BASE + "/" + encodeURIComponent(id), { method: "DELETE" });
}
