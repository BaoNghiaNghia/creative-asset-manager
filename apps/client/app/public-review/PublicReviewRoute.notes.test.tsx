// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api, type Annotation, type Asset } from "./api";
import { PublicReviewRoute } from "./PublicReviewRoute";
import { readReviewHistory } from "./viewHistory";

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
function note(id: string, text: string, authorName = "Guest ABCD"): Annotation {
  return {
    id, author: { display_name: authorName },
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
  localStorage.clear();
});

describe("Public Review folder deep links", () => {
  it("restores a deep-linked folder and keeps browser history in sync", async () => {
    const fakeLocation = { pathname: "/share/share-1/folder/nested", hash: "", origin: "https://review.example.test" };
    const pushState = vi.fn((_state: unknown, _title: string, url?: string | URL | null) => { if (url) fakeLocation.pathname = String(url); });
    const replaceState = vi.fn((_state: unknown, _title: string, url?: string | URL | null) => { if (url) fakeLocation.pathname = String(url); });
    vi.stubGlobal("location", fakeLocation);
    vi.stubGlobal("history", { pushState, replaceState });
    vi.spyOn(api, "bootstrap").mockResolvedValue({ public_id: "share-1", name: "Review", allow_comments: true, allow_download: false, expires_at: null });
    const rootFolder = { source_id: "source", folder_id: "root", name: "Root" };
    const nestedFolder = { source_id: "source", folder_id: "nested", name: "Nested" };
    vi.spyOn(api, "folders").mockResolvedValue({ items: [rootFolder] });
    const resolveFolder = vi.spyOn(api, "folder").mockResolvedValue({ folder: nestedFolder, trail: [rootFolder, nestedFolder] });
    vi.spyOn(api, "children").mockImplementation(async (_id, folder) => folder.folder_id === "nested"
      ? { items: [assets[1]], next_offset: null }
      : { items: [{ ...nestedFolder, kind: "folder" as const }, assets[0]], next_offset: null });

    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => {
      root.render(<PublicReviewRoute/>);
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(resolveFolder).toHaveBeenCalledWith("share-1", "nested");
    expect(host.querySelector(".public-folder-header h2")?.textContent).toBe("Nested");

    fakeLocation.pathname = "/share/share-1";
    await act(async () => {
      window.dispatchEvent(new PopStateEvent("popstate"));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(host.querySelector(".public-folder-header h2")?.textContent).toBe("Root");

    await click(host.querySelector(".public-folder-card"));
    expect(pushState).toHaveBeenLastCalledWith(null, "", "/share/share-1/folder/nested");
    expect(host.querySelector(".public-folder-header h2")?.textContent).toBe("Nested");

    await act(async () => root.unmount());
  });
});

describe("Public Review card comment badges", () => {
  it("shows counts on image and video comment actions and caps the visible badge at 99+", async () => {
    vi.stubGlobal("location", { pathname: "/share/share-1", hash: "", origin: "https://review.example.test" });
    vi.spyOn(api, "bootstrap").mockResolvedValue({ public_id: "share-1", name: "Review", allow_comments: true, allow_download: true, expires_at: null });
    vi.spyOn(api, "folders").mockResolvedValue({ items: [{ source_id: "source", folder_id: "root", name: "Root" }] });
    vi.spyOn(api, "children").mockResolvedValue({
      items: [
        { ...assets[0], annotation_count: 3 },
        { ...assets[1], annotation_count: 120 },
        { ...assets[2], annotation_count: 0 },
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
    expect(host.querySelector('[aria-label="Add note to c.jpg"]')).not.toBeNull();
    expect(Array.from(host.querySelectorAll(".public-card-comment-count")).map(node => node.textContent)).toEqual(["3", "99+"]);
    expect(host.querySelectorAll(".public-card-actions.has-comments")).toHaveLength(2);
    expect(host.querySelectorAll(".public-media-card.has-comments")).toHaveLength(2);
    expect(host.querySelector('[aria-label="Actions for c.jpg"]')?.classList.contains("has-comments")).toBe(false);
    expect(host.querySelector('[aria-label="Open c.jpg"]')?.closest(".public-media-card")?.classList.contains("has-comments")).toBe(false);
    expect(Array.from(host.querySelectorAll(".public-card-copy .public-status")).some(node => node.textContent === "Shared")).toBe(false);
    expect(host.querySelectorAll(".public-card-check")).toHaveLength(0);
    expect(host.querySelectorAll(".public-card-actions .public-card-comment-action")).toHaveLength(3);
    expect(host.querySelectorAll(".public-card-hover-actions")).toHaveLength(3);
    expect(host.querySelectorAll(".public-card-hover-actions .public-card-share-action")).toHaveLength(3);
    expect(host.querySelectorAll<HTMLAnchorElement>(".public-card-hover-actions .public-card-download-action")).toHaveLength(3);
    expect(host.querySelector<HTMLAnchorElement>('[aria-label="Download a.jpg"]')?.getAttribute("href")).toContain("/api/public/review/share-1/assets/a/download?source_asset_id=source-a");
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

describe("Public Review device-local history", () => {
  it("records opened assets and reopens them from the History drawer", async () => {
    vi.stubGlobal("location", { pathname: "/share/share-1", hash: "", origin: "https://review.example.test" });
    vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
    vi.spyOn(api, "bootstrap").mockResolvedValue({ public_id: "share-1", name: "Review", allow_comments: true, allow_download: false, expires_at: null });
    vi.spyOn(api, "folders").mockResolvedValue({ items: [{ source_id: "source", folder_id: "root", name: "Root" }] });
    vi.spyOn(api, "children").mockResolvedValue({ items: assets.slice(0, 2), next_offset: null });
    vi.spyOn(api, "annotations").mockResolvedValue({ items: [] });

    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => {
      root.render(<PublicReviewRoute/>);
      await Promise.resolve();
      await Promise.resolve();
    });

    await click(host.querySelector('[aria-label="Open a.jpg"]'));
    await act(async () => { await Promise.resolve(); });
    expect(readReviewHistory("share-1").map(item => item.asset_id)).toEqual(["a"]);
    await click(host.querySelector('[aria-label="Close"]'));

    await click(host.querySelector('[title="History"]'));
    expect(host.querySelector('[aria-label="Open a.jpg from history"]')).not.toBeNull();
    expect(host.textContent).toContain("1 viewed · last 15 days");

    const outside = host.querySelector(".public-folder-detail")!;
    await act(async () => outside.dispatchEvent(new Event("pointerdown", { bubbles: true })));
    expect(host.querySelector('[title="History"]')).not.toBeNull();
    expect(host.querySelector('[aria-label="Open a.jpg from history"]')).toBeNull();

    await click(host.querySelector('[title="History"]'));
    await click(host.querySelector('[aria-label="Open a.jpg from history"]'));
    expect(host.querySelector('[aria-label="Review a.jpg"]')).not.toBeNull();
    expect(host.querySelector('[aria-label="Media 1 of 1"]')).not.toBeNull();

    await act(async () => root.unmount());
  });
});

describe("Public Review replies", () => {
  it("shows reply context and creates the comment under the selected parent", async () => {
    vi.stubGlobal("location", { pathname: "/share/share-1", hash: "", origin: "https://review.example.test" });
    vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
    vi.spyOn(api, "bootstrap").mockResolvedValue({ public_id: "share-1", name: "Review", allow_comments: true, allow_download: false, expires_at: null });
    vi.spyOn(api, "folders").mockResolvedValue({ items: [{ source_id: "source", folder_id: "root", name: "Root" }] });
    vi.spyOn(api, "children").mockResolvedValue({ items: [assets[0]], next_offset: null });
    vi.spyOn(api, "annotations").mockResolvedValue({ items: [note("note-a", "Parent message for reply", "Tan Le")] });
    const reply = { ...note("reply-a", "Child reply", "Thuy Linh"), parent_annotation_id: "note-a" };
    const create = vi.spyOn(api, "create").mockResolvedValue(reply);

    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => {
      root.render(<PublicReviewRoute/>);
      await Promise.resolve();
      await Promise.resolve();
    });
    await click(host.querySelector('[aria-label="Open a.jpg"]'));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    const replyButton = host.querySelector<HTMLButtonElement>('[aria-label="Reply to Tan Le"]');
    await click(replyButton);
    expect(replyButton?.getAttribute("aria-pressed")).toBe("true");
    expect(host.querySelector('[aria-label="Replying to Tan Le"]')?.textContent).toContain("Parent message for reply");
    expect(host.querySelector(".public-review-composer")?.classList.contains("is-replying")).toBe(true);

    const draft = host.querySelector<HTMLInputElement>('[aria-label="Comment draft"]')!;
    draft.value = "Child reply";
    await click(host.querySelector('button[type="submit"]'));
    await act(async () => { await Promise.resolve(); });

    expect(create).toHaveBeenCalledWith(
      "share-1",
      assets[0],
      expect.any(Object),
      expect.objectContaining({ parentAnnotationId: "note-a" }),
    );
    expect(host.textContent).toContain("Child reply");
    expect(host.querySelector(".public-comment-replies")).not.toBeNull();
    const rootAvatar = host.querySelector<HTMLElement>(".public-comment > .public-comment-avatar");
    const replyAvatar = host.querySelector<HTMLElement>(".public-comment-reply .public-comment-avatar");
    const rootTone = Array.from(rootAvatar?.classList || []).find(value => value.startsWith("public-avatar-tone-"));
    const replyTone = Array.from(replyAvatar?.classList || []).find(value => value.startsWith("public-avatar-tone-"));
    expect(rootTone).toBeTruthy();
    expect(replyTone).toBeTruthy();
    expect(rootTone).not.toBe(replyTone);
    expect(host.querySelector(".public-reply-context")).toBeNull();
    await act(async () => root.unmount());
  });
});
