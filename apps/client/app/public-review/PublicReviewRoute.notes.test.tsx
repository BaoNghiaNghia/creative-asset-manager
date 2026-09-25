// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api, type Annotation, type Asset } from "./api";
import { PublicReviewRoute } from "./PublicReviewRoute";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("./PublicSourceTree", () => ({ PublicSourceTree: () => null }));
vi.mock("./PublicAssetThumbnail", () => ({
  PublicAssetThumbnail: () => null,
  PublicGridSkeleton: () => null,
}));
vi.mock("./AnnotationEditor", () => ({
  AnnotationEditor: ({ onSubmit }: { onSubmit: (content: unknown) => Promise<void> }) =>
    <form onSubmit={event => {
      event.preventDefault();
      const input = event.currentTarget.elements.namedItem("comment") as HTMLInputElement;
      void onSubmit({ type: "doc", content: [{ type: "paragraph", content: [{ type: "text", text: input.value }] }] });
    }}>
      <input name="comment" aria-label="Comment draft"/>
      <button type="submit">Post</button>
    </form>,
}));

const assets: Asset[] = [
  { kind: "asset", asset_id: "a", source_asset_id: "source-a", filename: "a.jpg", media_type: "image/jpeg", thumbnail_url: "/thumb/a", preview_url: "/preview/a" },
  { kind: "asset", asset_id: "b", source_asset_id: "source-b", filename: "b.mp4", media_type: "video/mp4", thumbnail_url: "/thumb/b", preview_url: "/preview/b" },
  { kind: "asset", asset_id: "c", source_asset_id: "source-c", filename: "c.jpg", media_type: "image/jpeg", thumbnail_url: "/thumb/c", preview_url: "/preview/c" },
];

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}
function note(id: string, text: string): Annotation {
  return {
    id, author: { display_name: "Guest ABCD" },
    content_json: { type: "doc", content: [{ type: "paragraph", content: [{ type: "text", text }] }] },
    plain_text: text, parent_annotation_id: null, anchor_x: null, anchor_y: null,
    created_at: "2026-09-19T00:00:00Z", updated_at: "2026-09-19T00:00:00Z",
    can_edit: true, can_delete: true,
  };
}
async function click(element: Element | null) {
  expect(element).not.toBeNull();
  await act(async () => element!.dispatchEvent(new MouseEvent("click", { bubbles: true })));
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  document.body.replaceChildren();
});

describe("Public Review card comment badges", () => {
  it("shows counts on image and video comment actions and caps the visible badge at 99+", async () => {
    vi.stubGlobal("location", { pathname: "/share/share-1", hash: "", origin: "https://review.example.test" });
    vi.spyOn(api, "bootstrap").mockResolvedValue({ public_id: "share-1", name: "Review", allow_comments: true, allow_download: false, expires_at: null });
    vi.spyOn(api, "folders").mockResolvedValue({ items: [{ source_id: "source", folder_id: "root", name: "Root" }] });
    vi.spyOn(api, "children").mockResolvedValue({
      items: [
        { ...assets[0], annotation_count: 3 },
        { ...assets[1], annotation_count: 120 },
      ],
      next_offset: null,
    });
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => {
      root.render(<PublicReviewRoute/>);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(host.querySelector('[aria-label="3 comments on a.jpg"]')).not.toBeNull();
    expect(host.querySelector('[aria-label="120 comments on b.mp4"]')).not.toBeNull();
    expect(Array.from(host.querySelectorAll(".public-card-comment-count")).map(node => node.textContent)).toEqual(["3", "99+"]);
    await act(async () => root.unmount());
  });
});

describe("Review notes follow the selected asset", () => {
  it("clears stale notes and drafts, ignores late responses, and creates on the active asset", async () => {
    vi.stubGlobal("location", { pathname: "/share/share-1", hash: "", origin: "https://review.example.test" });
    vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
    vi.spyOn(api, "bootstrap").mockResolvedValue({ public_id: "share-1", name: "Review", allow_comments: true, allow_download: false, expires_at: null });
    vi.spyOn(api, "folders").mockResolvedValue({ items: [{ source_id: "source", folder_id: "root", name: "Root" }] });
    vi.spyOn(api, "children").mockResolvedValue({ items: assets, next_offset: null });
    const pending = Object.fromEntries(assets.map(asset => [asset.asset_id, deferred<{ items: Annotation[] }>()]));
    const annotations = vi.spyOn(api, "annotations").mockImplementation((_id, asset) => pending[asset.asset_id].promise);
    const create = vi.spyOn(api, "create").mockResolvedValue(note("new-c", "Comment on C"));
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<PublicReviewRoute/>));
    await click(host.querySelector('[aria-label="Open a.jpg"]'));
    expect(host.querySelector('[aria-label="Loading comments"]')).not.toBeNull();
    await act(async () => pending.a.resolve({ items: [note("note-a", "Only A note")] }));
    expect(host.textContent).toContain("Only A note");

    const draft = host.querySelector<HTMLInputElement>('[aria-label="Comment draft"]')!;
    await act(async () => {
      draft.value = "Draft for A";
      draft.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await click(host.querySelector('[aria-label="Next asset"]'));
    expect(host.textContent).not.toContain("Only A note");
    expect(host.querySelector('[aria-label="Loading comments"]')).not.toBeNull();
    expect(host.querySelector<HTMLInputElement>('[aria-label="Comment draft"]')!.value).toBe("");
    expect(host.querySelectorAll(".public-media-position span.active")).toHaveLength(1);
    await click(host.querySelector('[aria-label="Next asset"]'));
    await act(async () => pending.b.resolve({ items: [note("note-b", "Late B note")] }));
    expect(host.textContent).not.toContain("Late B note");
    expect(host.querySelector('[aria-label="Loading comments"]')).not.toBeNull();
    await act(async () => pending.c.resolve({ items: [] }));
    expect(host.textContent).toContain("No comments yet");
    expect(annotations.mock.calls.map(([, asset]) => asset.asset_id)).toEqual(["a", "b", "c"]);
    expect(host.querySelectorAll(".public-media-position span.active")).toHaveLength(1);

    const currentDraft = host.querySelector<HTMLInputElement>('[aria-label="Comment draft"]')!;
    currentDraft.value = "Comment on C";
    await click(host.querySelector('button[type="submit"]'));
    expect(create).toHaveBeenCalledWith("share-1", assets[2], expect.any(Object), expect.any(Object));
    await act(async () => root.unmount());
  });
});
