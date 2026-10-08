// @vitest-environment jsdom
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { Asset } from "../types";
import { VisualSearchPanel } from "../components/VisualSearchPanel";
import { VISUAL_HISTORY_LIMIT, curateVisualHistory, recentAssetRecord, recentUploadRecord } from "./useRecentVisualSearches";

function asset(id: string): Asset {
  return { provider: "google-drive", id, internal_asset_id: id, external_source_id: "drive-a", name: id + ".jpg", kind: "image", mime_type: "image/jpeg" };
}

function panel(images: Parameters<typeof VisualSearchPanel>[0]["recentImages"] = []) {
  return renderToStaticMarkup(createElement(VisualSearchPanel, {
    scope: "all", canSearchAllResources: true, hasCurrentSource: true, hasCurrentFolder: false,
    reference: null, loading: false, error: "", refinement: "",
    onScopeChange: () => undefined, onRefinementChange: () => undefined, onUpload: () => undefined,
    onApplyCrop: () => undefined, onRetry: () => undefined, onClose: () => undefined,
    showRecentImages: true, recentImages: images,
  }));
}

describe("per-user visual search history", () => {
  it("does not mix accounts, removes repeats, and orders newer searches first", () => {
    const older = recentAssetRecord("alice", asset("old"), 10);
    const newer = recentAssetRecord("alice", asset("new"), 40);
    const repeat = recentAssetRecord("alice", asset("old"), 50);
    const otherUser = recentAssetRecord("bob", asset("private"), 100);
    expect(curateVisualHistory([older, otherUser, newer, repeat], "alice").map(item => item.key))
      .toEqual([repeat.key, newer.key]);
    expect(curateVisualHistory([older, otherUser, newer, repeat], "bob").map(item => item.key))
      .toEqual([otherUser.key]);
  });

  it("retains up to the configured history limit without relying on current folder contents", () => {
    const records = Array.from({ length: VISUAL_HISTORY_LIMIT + 5 }, (_, index) => recentAssetRecord("alice", asset(String(index)), index));
    expect(curateVisualHistory(records, "alice")).toHaveLength(VISUAL_HISTORY_LIMIT);
    expect(curateVisualHistory(records, "alice")[0].kind).toBe("asset");
  });

  it("recognizes user uploads as searchable image history rather than folder assets", () => {
    const file = new File(["image"], "photo.png", { type: "image/png", lastModified: 42 });
    const record = recentUploadRecord("alice", file, 10);
    expect(curateVisualHistory([record], "alice")).toEqual([record]);
    expect(curateVisualHistory([record], "bob")).toEqual([]);
  });

  it("always renders the recent images section, including an empty state", () => {
    const markup = panel([]);
    expect(markup).toContain("Your recent images");
    expect(markup).toContain("Your previous visual searches will appear here.");
  });

  it("shows the first eight images by default, with View all for longer history", () => {
    const images = Array.from({ length: 10 }, (_, index) => ({
      key: "item-" + index, kind: "asset" as const, asset: asset(String(index)), previewUrl: "/thumbnail/" + index,
    }));
    const markup = panel(images);
    expect(markup).toContain("View all");
    expect(markup).toContain("/thumbnail/7");
    expect(markup).not.toContain("/thumbnail/8");
  });

  it("keeps history out of the anonymous public-review visual search modal", () => {
    const markup = renderToStaticMarkup(createElement(VisualSearchPanel, {
      scope: "all", canSearchAllResources: true, hasCurrentSource: false, hasCurrentFolder: false,
      reference: null, loading: false, error: "", refinement: "",
      onScopeChange: () => undefined, onRefinementChange: () => undefined, onUpload: () => undefined,
      onApplyCrop: () => undefined, onRetry: () => undefined, onClose: () => undefined,
    }));
    expect(markup).not.toContain("Your recent images");
  });
});
