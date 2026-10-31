import { RrugcApiError } from "../api";
import type {
  TypographyDna, TypographyReference, TypographyReferenceFilters, TypographyReferencePage,
  TypographyReferencePatch, TypographyUploadBatch, TypographyRights, TypographyPrivacy,
  TypographyPreviewResponse, TypographyAnalysisState,
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

export function getTypographyAnalysisStatus(id: string, signal?: AbortSignal): Promise<TypographyAnalysisState> {
  return jsonRequest(BASE + "/" + encodeURIComponent(id) + "/analysis", { signal });
}

/** Explicit, rights-gated one-shot G3 job. No analyzer call when its pilot worker is OFF. */
export function runTypographyAnalysis(id: string): Promise<TypographyAnalysisState & { outcome: string }> {
  return jsonRequest(BASE + "/" + encodeURIComponent(id) + "/analyze-and-run", { method: "POST" });
}

export function reviewTypographyDna(
  referenceId: string, dnaId: string,
  decision: "accepted" | "needs_review" | "rejected",
  reasonCode: string,
): Promise<{ id: string; review: NonNullable<TypographyDna["review"]> }> {
  return jsonRequest(BASE + "/" + encodeURIComponent(referenceId)
    + "/dna/" + encodeURIComponent(dnaId) + "/review", {
      method: "PATCH", body: JSON.stringify({ decision, reason_code: reasonCode }),
    });
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
/** Creative Engine V3 P1 preview is a read-only projection of existing V1 briefs.
 * No job, image, source selection, or feedback write is performed.
 */
export type CreativeV3ConceptPreview = {
  id: string;
  source_mode: "safe" | "creative_twist" | "explore";
  mode_label: string;
  name: string;
  description: string;
  creative_hook: string;
  meaning_rationale: string;
  familiar_format: string;
  twist_description: string;
  typography_plan: string;
  embroidery_plan: string;
  clarity_assessment_unverified: string;
  embroidery_fit_unverified: string;
  risks: string[];
  mechanism: string | null;
  mechanism_verified: false;
  text_spans: { text: string; role: "locked_quote" }[];
  design_blueprint_status: "not_composed";
  quality: { status: "needs_review"; reasons: string[] };
};
export type CreativeV3Preview = {
  schema_version: "creative-v3.0-preview-v1";
  keyword_id: string;
  keyword_exact: string;
  source_status: string;
  planner_status: "v1_bridge_preview_only" | "v1_brief_not_ready";
  source?: { brief_prompt_version: string; brief_content_sha256: string;
    style_dna_id: null; reference_rights_reviewed: false };
  intent_hint?: string;
  concepts: CreativeV3ConceptPreview[];
  exact_text_locked?: true;
  mode_policy?: { balanced: "creative_twist"; explore: "explore";
    exploit: "requires_explicit_approved_feedback" };
  preview_only: true;
  job_created: false;
  can_generate_artwork: false;
};

const CREATIVE_V3_BASE = "/api/v1/realistic-review-ugc/keyword-creative-briefs";

export function getCreativeV3Status(signal?: AbortSignal): Promise<{ available: boolean; schema_version: string }> {
  return jsonRequest(CREATIVE_V3_BASE + "/v3/status", { signal });
}

export function getCreativeV3Preview(keywordId: string, signal?: AbortSignal): Promise<CreativeV3Preview> {
  return jsonRequest(CREATIVE_V3_BASE + "/" + encodeURIComponent(keywordId) + "/v3-preview", { signal });
}