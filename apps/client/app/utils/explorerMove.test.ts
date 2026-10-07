import { describe, expect, it } from "vitest";
import type { Asset } from "../types";
import {
  ASSET_EXPLORER_MOVE_MIME,
  buildExplorerMoveDragPayload,
  decodeExplorerMoveDragPayload,
  dragTypesIncludeExplorerMove,
  encodeExplorerMoveDragPayload,
  explorerMoveTargetAllowed,
} from "./explorerMove";

function asset(id: string, overrides: Partial<Asset> = {}): Asset {
  return {
    provider: "google-drive",
    id,
    name: id,
    kind: "image",
    mime_type: "image/jpeg",
    external_source_id: "source-a",
    ...overrides,
  };
}

describe("Explorer internal move payload", () => {
  it("deduplicates selected items and keeps source identity", () => {
    expect(buildExplorerMoveDragPayload([
      asset("a"),
      asset("a"),
      asset("b", { kind: "folder", mime_type: "application/vnd.google-apps.folder" }),
    ], "source-a")).toEqual({
      itemIds: ["a", "b"],
      provider: "google-drive",
      externalSourceId: "source-a",
    });
  });

  it("rejects mixed providers and mixed Drive sources", () => {
    expect(buildExplorerMoveDragPayload([
      asset("a"),
      asset("b", { provider: "onedrive" }),
    ])).toBeNull();
    expect(buildExplorerMoveDragPayload([
      asset("a"),
      asset("b", { external_source_id: "source-b" }),
    ])).toBeNull();
  });

  it("round-trips a safe custom drag MIME payload", () => {
    const payload = buildExplorerMoveDragPayload([asset("a")])!;
    expect(dragTypesIncludeExplorerMove([ASSET_EXPLORER_MOVE_MIME])).toBe(true);
    expect(decodeExplorerMoveDragPayload(encodeExplorerMoveDragPayload(payload))).toEqual(payload);
    expect(decodeExplorerMoveDragPayload("{broken")).toBeNull();
  });

  it("accepts only another folder in the same provider/source", () => {
    const payload = buildExplorerMoveDragPayload([asset("a")])!;
    expect(explorerMoveTargetAllowed(
      payload,
      asset("folder-b", { kind: "folder", mime_type: "application/vnd.google-apps.folder" }),
      "source-a",
    )).toBe(true);
    expect(explorerMoveTargetAllowed(
      payload,
      asset("a", { kind: "folder", mime_type: "application/vnd.google-apps.folder" }),
      "source-a",
    )).toBe(false);
    expect(explorerMoveTargetAllowed(payload, asset("file-b"), "source-a")).toBe(false);
    expect(explorerMoveTargetAllowed(
      payload,
      asset("folder-b", {
        kind: "folder",
        mime_type: "application/vnd.google-apps.folder",
        external_source_id: "source-b",
      }),
      "source-a",
    )).toBe(false);
  });
});
