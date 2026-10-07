import type { Asset, Provider } from "../types";

export const ASSET_EXPLORER_MOVE_MIME = "application/x-cam-explorer-move";

export type ExplorerMoveDragPayload = {
  itemIds: string[];
  provider: Provider;
  externalSourceId: string | null;
};

export function buildExplorerMoveDragPayload(
  items: readonly Asset[],
  activeExternalSourceId?: string | null,
): ExplorerMoveDragPayload | null {
  const unique = [...new Map(items.filter(item => Boolean(item.id)).map(item => [item.id, item])).values()];
  if (!unique.length) return null;

  const provider = unique[0].provider;
  if (!unique.every(item => item.provider === provider)) return null;

  const knownSourceIds = new Set(
    unique
      .map(item => item.external_source_id || activeExternalSourceId || null)
      .filter((value): value is string => Boolean(value)),
  );
  if (knownSourceIds.size > 1) return null;

  return {
    itemIds: unique.map(item => item.id),
    provider,
    externalSourceId: [...knownSourceIds][0] || activeExternalSourceId || null,
  };
}

export function encodeExplorerMoveDragPayload(payload: ExplorerMoveDragPayload): string {
  return JSON.stringify(payload);
}

export function decodeExplorerMoveDragPayload(value: string): ExplorerMoveDragPayload | null {
  if (!value) return null;
  try {
    const parsed = JSON.parse(value) as Partial<ExplorerMoveDragPayload>;
    if (!Array.isArray(parsed.itemIds) || !parsed.itemIds.length) return null;
    const itemIds = [...new Set(parsed.itemIds.filter((item): item is string => typeof item === "string" && item.trim().length > 0))];
    if (!itemIds.length) return null;
    if (!["google-drive", "onedrive", "sharepoint"].includes(String(parsed.provider))) return null;
    return {
      itemIds,
      provider: parsed.provider as Provider,
      externalSourceId: typeof parsed.externalSourceId === "string" && parsed.externalSourceId
        ? parsed.externalSourceId
        : null,
    };
  } catch {
    return null;
  }
}

export function dragTypesIncludeExplorerMove(types: Iterable<string>): boolean {
  return [...types].includes(ASSET_EXPLORER_MOVE_MIME);
}

export function explorerMoveTargetAllowed(
  payload: ExplorerMoveDragPayload,
  destination: Pick<Asset, "id" | "kind" | "provider" | "external_source_id">,
  activeExternalSourceId?: string | null,
): boolean {
  if (destination.kind !== "folder" || destination.provider !== payload.provider) return false;
  if (payload.itemIds.includes(destination.id)) return false;

  const destinationSourceId = destination.external_source_id || activeExternalSourceId || null;
  if (payload.externalSourceId && destinationSourceId && payload.externalSourceId !== destinationSourceId) return false;
  return true;
}
