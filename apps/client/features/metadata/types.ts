import type { Provider } from "../../app/types";

export type AiProviderId = "gemini" | "openai";
export type AiProcessingMode = "single" | "batch";

export type AiModelCapability = {
  id: string;
  label: string;
  supports_single: boolean;
  supports_batch: boolean;
};
export type AiProviderCapability = {
  id: AiProviderId;
  label: string;
  enabled: boolean;
  models: AiModelCapability[];
  default_model: string;
  supported_modes: AiProcessingMode[];
};
export type AiCapabilities = { providers: AiProviderCapability[] };
export type AiAnalysisSelection = {
  provider: AiProviderId;
  processingMode: AiProcessingMode;
  model: string;
};
export type SubmitAnalysisInput = {
  assetIds: string[];
  sourceProvider: Provider;
  metadataProfile: string;
  metadataProfileVersion?: string | null;
  provider: AiProviderId;
  processingMode: AiProcessingMode;
  model: string;
  force: boolean;
};
export type AnalysisAcceptance =
  | "accepted" | "already_exists" | "invalid_asset" | "unauthorized"
  | "provider_unavailable" | "budget_preflight_failed";
export type AnalysisRequestItem = {
  asset_id: string;
  acceptance_status: AnalysisAcceptance;
  analysis_id: string | null;
  job_id: string | null;
  error_code: string | null;
  error_message: string | null;
  processing_status?: string;
  batch_id?: string | null;
  provider_batch_id?: string | null;
};
export type SingleAnalysisAccepted = {
  kind: "single";
  analysis_id: string;
  job_id: string;
  provider: AiProviderId;
  model: string;
  processing_mode: AiProcessingMode;
  status: "accepted";
};
export type BulkAnalysisAccepted = {
  kind: "bulk";
  request_id: string;
  status: "accepted";
  provider: AiProviderId;
  model: string;
  processing_mode: AiProcessingMode;
  analysis_count: number;
  warning: string | null;
  items: AnalysisRequestItem[];
};
export type AnalysisSubmission = SingleAnalysisAccepted | BulkAnalysisAccepted;
export type AnalysisRequestStatus = {
  request_id: string;
  status: string;
  provider: AiProviderId;
  model: string;
  processing_mode: AiProcessingMode;
  analysis_count: number;
  batch_count: number;
  queued: number;
  running: number;
  completed: number;
  failed: number;
  cancelled: number;
  warning: string | null;
  items: AnalysisRequestItem[];
  created_at: string;
  updated_at: string;
  cancelled_at: string | null;
};
export type AnalysisProgress = {
  provider: AiProviderId;
  model: string;
  processingMode: AiProcessingMode;
  accepted: number;
  queued: number;
  running: number;
  completed: number;
  failed: number;
  budgetBlocked: number;
  providerBatchStatus?: string;
};
