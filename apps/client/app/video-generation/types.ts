export type GenerationStatus = "queued" | "preparing" | "submitted" | "running" | "submission_unknown" | "storing" | "completed" | "failed" | "cancelled";
export type VideoGenerationCapabilities = { enabled: boolean; provider: string; models: string[]; aspect_ratios: string[]; durations: number[]; max_references: number; allowed_reference_mime_types: string[]; max_reference_bytes: number; max_reference_total_bytes: number; };
export type VideoGenerationRequest = { client_request_id: string; prompt: string; model: string; aspect_ratio: string; duration_seconds: number; reference_asset_ids: string[]; };
export type GenerationError = { code: string; message: string };
export type VideoGeneration = { id: string; status: GenerationStatus; provider: string; model: string; prompt: string; aspect_ratio: string; duration_seconds: number; reference_asset_ids: string[]; output_asset_id: string | null; error: GenerationError | null; created_at: string; submitted_at: string | null; completed_at: string | null; };
export type ReferenceAsset = { id: string; name: string; mime_type: string; thumbnail_url?: string; internal_asset_id?: string; kind: string; };
export class VideoGenerationApiError extends Error { constructor(message: string, readonly status: number, readonly code: string) { super(message); } }
