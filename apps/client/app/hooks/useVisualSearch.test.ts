// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { VisualSearchPanel } from "../components/VisualSearchPanel";
import type { Asset, Provider } from "../types";
import { DEFAULT_VISUAL_SEARCH_SCOPE, committedVisualQuery, defaultVisualSearchScope, normalizeCrop, useVisualSearch, visualByAssetRequest, visualErrorMessage, visualUploadParams } from "./useVisualSearch";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type VisualSearchHook = ReturnType<typeof useVisualSearch>;
let currentHook: VisualSearchHook | null = null;

function HookProbe({ provider, externalSourceId, folderId, canSearchAllResources }: { provider: Provider | null; externalSourceId: string | null; folderId: string | null; canSearchAllResources: boolean | null }) {
  currentHook = useVisualSearch(provider, externalSourceId, folderId, canSearchAllResources);
  return null;
}

function referenceAsset(): Asset {
  return { id: "asset-a", internal_asset_id: "asset-a", provider: "google-drive", external_source_id: "source-a", name: "reference.jpg", kind: "image", mime_type: "image/jpeg" };
}

function okResponse(nextCursor: string | null = null): Response {
  return { ok: true, json: async () => ({ query_kind: "asset", items: [], next_cursor: nextCursor }) } as Response;
}

async function settle(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}

afterEach(() => {
  currentHook = null;
  vi.unstubAllGlobals();
});

describe("visual search client helpers", () => {
  it("clamps normalized crop inside image bounds", () => {
    expect(normalizeCrop({ x: 0.9, y: -1, width: 0.8, height: 2 })).toEqual({
      x: 0.9, y: 0, width: 0.09999999999999998, height: 1,
    });
  });

  it("uses controlled API detail messages", () => {
    expect(visualErrorMessage({ detail: { message: "Visual search is busy." } })).toBe("Visual search is busy.");
  });

  it("snapshots normalized crop, text, and scope for a committed request", () => {
    expect(committedVisualQuery({ x: 0.9, y: 0, width: 0.8, height: 1 }, " outdoor ", "source", "google-drive", "source-a", null)).toEqual({
      crop: { x: 0.9, y: 0, width: 0.09999999999999998, height: 1 },
      text: "outdoor", scope: "source", provider: "google-drive", externalSourceId: "source-a", folderId: null,
    });
  });

  it("defaults eligible searches to all resources", () => { expect(DEFAULT_VISUAL_SEARCH_SCOPE).toBe("all"); });

  it("omits explorer source for all resources", () => { expect(committedVisualQuery(undefined, "", "all", "google-drive", "source-a", "folder-a")).toMatchObject({ scope: "all", provider: null, externalSourceId: null, folderId: null }); });

  it("commits the selected folder only for folder scope", () => { expect(committedVisualQuery(undefined, "", "folder", "google-drive", "source-a", "folder-a")).toMatchObject({ scope: "folder", provider: "google-drive", externalSourceId: "source-a", folderId: "folder-a" }); });


  it("selects only an executable default scope from authenticated capability and context", () => {
    expect(defaultVisualSearchScope(true, true, true)).toBe("all");
    expect(defaultVisualSearchScope(false, true, true)).toBe("folder");
    expect(defaultVisualSearchScope(false, true, false)).toBe("source");
    expect(defaultVisualSearchScope(false, false, false)).toBeNull();
    expect(defaultVisualSearchScope(null, true, true)).toBeNull();
  });

  it("renders all resources unavailable for viewers while retaining safe contextual options", () => {
    const markup = renderToStaticMarkup(createElement(VisualSearchPanel, {
      scope: "folder", canSearchAllResources: false, hasCurrentSource: true, hasCurrentFolder: true,
      reference: null, loading: false, error: "", refinement: "",
      onScopeChange: () => undefined, onRefinementChange: () => undefined, onUpload: () => undefined,
      onApplyCrop: () => undefined, onRetry: () => undefined, onClose: () => undefined,
    }));
    expect(markup).toContain("value=\"all\" disabled=\"\"");
    expect(markup).toContain("value=\"folder\" selected=\"\"");
    const globalMarkup = renderToStaticMarkup(createElement(VisualSearchPanel, {
      scope: "all", canSearchAllResources: true, hasCurrentSource: false, hasCurrentFolder: false,
      reference: null, loading: false, error: "", refinement: "",
      onScopeChange: () => undefined, onRefinementChange: () => undefined, onUpload: () => undefined,
      onApplyCrop: () => undefined, onRetry: () => undefined, onClose: () => undefined,
    }));
    expect(globalMarkup).toContain("value=\"all\" selected=\"\"");
    expect(globalMarkup).not.toContain("value=\"all\" disabled=\"\"");
  });

  it("serializes committed scope consistently for asset, upload, crop, hybrid, and Load More", () => {
    const committed = committedVisualQuery({ x: 0, y: 0, width: 1, height: 1 }, " outdoor ", "source", "google-drive", "source-a", null);
    const draft = committedVisualQuery(undefined, "", "folder", "google-drive", "source-b", "folder-b");
    expect(visualByAssetRequest("asset-a", committed, "cursor-a")).toMatchObject({ asset_id: "asset-a", scope: "source", source_provider: "google-drive", external_source_id: "source-a", cursor: "cursor-a", text: "outdoor", crop: { width: 1 } });
    expect(Object.fromEntries(visualUploadParams(committed, "cursor-a"))).toMatchObject({ scope: "source", source_provider: "google-drive", external_source_id: "source-a", cursor: "cursor-a", text: "outdoor" });
    expect(visualByAssetRequest("asset-a", committed, "cursor-a")).not.toMatchObject({ scope: draft.scope, external_source_id: draft.externalSourceId });
  });

  it("defaults a viewer to the current folder and sends that authorized scope", async () => {
    const fetchMock = vi.fn().mockResolvedValue(okResponse());
    vi.stubGlobal("fetch", fetchMock);
    const container = document.createElement("div");
    const root = createRoot(container);
    await act(async () => { root.render(createElement(HookProbe, { provider: "google-drive", externalSourceId: "source-a", folderId: "folder-a", canSearchAllResources: false })); await settle(); });
    expect(currentHook?.scope).toBe("folder");
    await act(async () => { currentHook?.chooseAsset(referenceAsset()); await settle(); });
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({ scope: "folder", source_provider: "google-drive", external_source_id: "source-a", folder_id: "folder-a" });
    await act(async () => { root.unmount(); });
  });

  it("defaults a viewer without a folder to the current source", async () => {
    const fetchMock = vi.fn().mockResolvedValue(okResponse());
    vi.stubGlobal("fetch", fetchMock);
    const container = document.createElement("div");
    const root = createRoot(container);
    await act(async () => { root.render(createElement(HookProbe, { provider: "google-drive", externalSourceId: "source-a", folderId: null, canSearchAllResources: false })); await settle(); });
    expect(currentHook?.scope).toBe("source");
    await act(async () => { currentHook?.chooseAsset(referenceAsset()); await settle(); });
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({ scope: "source", source_provider: "google-drive", external_source_id: "source-a" });
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).not.toHaveProperty("folder_id");
    await act(async () => { root.unmount(); });
  });

  it("does not send a viewer request without an authorized context", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const container = document.createElement("div");
    const root = createRoot(container);
    await act(async () => { root.render(createElement(HookProbe, { provider: null, externalSourceId: null, folderId: null, canSearchAllResources: false })); await settle(); });
    expect(currentHook?.scope).toBeNull();
    await act(async () => { currentHook?.chooseAsset(referenceAsset()); await settle(); });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(currentHook?.error).toContain("authorized source or folder");
    await act(async () => { root.unmount(); });
  });

  it("uses the committed scope and identifiers for Load More after explorer context changes", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(okResponse("cursor-a")).mockResolvedValueOnce(okResponse());
    vi.stubGlobal("fetch", fetchMock);
    const container = document.createElement("div");
    const root = createRoot(container);
    await act(async () => { root.render(createElement(HookProbe, { provider: "google-drive", externalSourceId: "source-a", folderId: null, canSearchAllResources: false })); await settle(); });
    await act(async () => { currentHook?.chooseAsset(referenceAsset()); await settle(); });
    await act(async () => { root.render(createElement(HookProbe, { provider: "google-drive", externalSourceId: "source-b", folderId: "folder-b", canSearchAllResources: false })); await settle(); });
    await act(async () => { currentHook?.setScope("folder"); await settle(); });
    await act(async () => { currentHook?.loadMore(); await settle(); });
    const secondRequest = JSON.parse(fetchMock.mock.calls[1][1].body);
    expect(secondRequest).toMatchObject({ scope: "source", source_provider: "google-drive", external_source_id: "source-a", cursor: "cursor-a" });
    expect(secondRequest).not.toHaveProperty("folder_id");
    await act(async () => { root.unmount(); });
  });

});
