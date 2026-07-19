export type ExternalSourceType =
  | "google_drive"
  | "sharepoint"
  | "external_api";

export interface ExternalAssetCandidate {
  sourceType: ExternalSourceType;
  sourceId: string;
  externalAssetId: string;
  filename?: string;
  mimeType?: string;
  sizeBytes?: number;
  sourceCreatedAt?: string;
  sourceModifiedAt?: string;
  providerChecksum?: string;
  providerVersion?: string;
  sourceMetadata?: Record<string, unknown>;
}

export interface ListSourceChangesInput {
  sourceId: string;
  cursor?: string;
  pageSize?: number;
}

export interface SourceChangePage {
  changes: Array<{
    changeType: "created" | "updated" | "deleted" | "restored";
    externalAssetId: string;
    candidate?: ExternalAssetCandidate;
  }>;
  nextCursor?: string;
  hasMore: boolean;
}

export interface GetSourceAssetInput {
  sourceId: string;
  externalAssetId: string;
}

export interface OpenSourceAssetInput extends GetSourceAssetInput {
  rangeHeader?: string;
}

export interface AssetDownloadStream {
  body: ReadableStream<Uint8Array>;
  contentType?: string;
  contentLength?: number;
}

export interface AssetSourceProvider {
  listChanges(input: ListSourceChangesInput): Promise<SourceChangePage>;
  getAsset(input: GetSourceAssetInput): Promise<ExternalAssetCandidate>;
  openDownloadStream(input: OpenSourceAssetInput): Promise<AssetDownloadStream>;
}

export interface StoreAssetInput {
  tenantId: string;
  contentHash: string;
  body: ReadableStream<Uint8Array>;
  assetId: string;
  sizeBytes?: number;
  contentType?: string;
  filename?: string;
}

export interface StoredAsset {
  storageKey: string;
  contentHash: string;
  sizeBytes?: number;
  storageProvider?: string;
  remoteFileId?: string;
  remoteFolderId?: string;
  webUrl?: string;
}

export interface StoreMetadataSidecarInput {
  tenantId: string;
  assetId: string;
  metadata: Record<string, unknown>;
}

export interface StoredMetadataSidecar {
  storageKey: string;
}

export interface AssetStorageProvider {
  storeAsset(input: StoreAssetInput): Promise<StoredAsset>;
  storeMetadataSidecar(
    input: StoreMetadataSidecarInput
  ): Promise<StoredMetadataSidecar>;
}

export interface AiMetadataAnalysisInput {
  tenantId: string;
  assetId: string;
  contentType?: string;
  sourceUrl?: string;
  profile?: Record<string, unknown>;
}

export interface AiMetadataAnalysisResult {
  metadata: Record<string, unknown>;
  provider: string;
  model?: string;
  providerRequestId?: string;
}

export interface AiMetadataProvider {
  analyzeSingle(
    input: AiMetadataAnalysisInput
  ): Promise<AiMetadataAnalysisResult>;
}
