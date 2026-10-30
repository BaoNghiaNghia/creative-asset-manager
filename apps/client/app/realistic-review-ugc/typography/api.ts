import { RrugcApiError } from "../api";
import type {
  TypographyDna, TypographyReference, TypographyReferenceFilters, TypographyReferencePage,
  TypographyReferencePatch, TypographyUploadBatch, TypographyRights, TypographyPrivacy,
  TypographyPreviewResponse,
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

// Returns an HTTP 200 availability flag for authenticated users, even when
// Typography V2 is OFF. Do not probe protected reference routes to discover access.
export function getTypographyStatus(signal?: AbortSignal): Promise<{ available: boolean }> {
  return jsonRequest(BASE.replace(/\/references$/, "") + "/status", { signal });
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

/** G4 low-cost preview: no image generation, job, storage write or publication. */
export function previewTypographyStyle(
  input: { reference_id: string; keyword_id: string; count?: number }, signal?: AbortSignal,
): Promise<TypographyPreviewResponse> {
  return jsonRequest(BASE.replace(/\/references$/, "") + "/previews", {
    method: "POST", body: JSON.stringify({ ...input, count: input.count ?? 3 }), signal,
  });
}


export type ReviewedSelectionRequest = {
  keyword_id: string;
  reference_id: string;
  selected_slots: number[];
};
export type TypographySavedVersion = {
  version: number; slot: number; batch_id: string;
  created_at: string | null; size_bytes: number; private_only: true;
};
const TYPOGRAPHY_BASE = BASE.replace(/\/references$/, "");

export function saveReviewedTypographyBatch(batchId: string, input: ReviewedSelectionRequest) {
  return jsonRequest<{
    saved_count: number; new_count: number; items: { slot: number; version: number }[];
    published: false; stage2_jobs_created: 0;
  }>(TYPOGRAPHY_BASE + "/batches/" + encodeURIComponent(batchId) + "/save-reviewed", {
    method: "POST", body: JSON.stringify(input),
  });
}

export function listSavedTypographyV2(keywordId: string) {
  return jsonRequest<{ keyword_id: string; items: TypographySavedVersion[] }>(
    TYPOGRAPHY_BASE + "/history/" + encodeURIComponent(keywordId),
  );
}

export function handoffTypographyV2(keywordId: string, version: number, selected: boolean) {
  return jsonRequest<{
    selected: boolean; source_plan_id: string | null;
    stage2_jobs_created: 0; scout_started: false;
  }>(TYPOGRAPHY_BASE + "/stage2/handoff", {
    method: "POST", body: JSON.stringify({ keyword_id: keywordId, version, selected }),
  });
}
