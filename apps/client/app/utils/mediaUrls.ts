import type { Asset } from "../types";
import { isAvifAsset } from "./fileType";

export type AssetUrlInput = Pick<Asset, "id" | "provider" | "external_source_id">
  & Partial<Pick<Asset, "name" | "mime_type">>;

export function explorerAssetUrl(item: AssetUrlInput, endpoint: "media" | "thumbnail" | "preview"): string {
  const parameters = new URLSearchParams({ provider: item.provider });
  if (item.external_source_id) parameters.set("external_source_id", item.external_source_id);
  return "/api/explorer/" + endpoint + "/" + encodeURIComponent(item.id) + "?" + parameters.toString();
}

export function assetPreviewUrl(item: AssetUrlInput): string {
  return explorerAssetUrl(item, isAvifAsset(item) ? "preview" : "media");
}
