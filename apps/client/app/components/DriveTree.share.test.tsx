// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Asset } from "../types";
import { DriveTreeNode } from "./DriveTree";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const folder = (id: string, name: string): Asset => ({
  provider: "google-drive",
  id,
  name,
  kind: "folder",
  mime_type: "application/vnd.google-apps.folder",
  has_children: false,
});

afterEach(() => {
  vi.restoreAllMocks();
  document.body.replaceChildren();
});

describe("DriveTreeNode shared-folder actions", () => {
  it("renders a three-dot trigger only for an exact active share mapping and opens both actions", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<DriveTreeNode
      node={folder("folder-1", "Shared folder")}
      ancestors={[]}
      activePathIds={new Set()}
      childrenByParent={{ "folder-1": [] }}
      expanded={new Set()}
      loadingNodes={new Set()}
      onOpen={() => undefined}
      onToggle={() => undefined}
      onPrefetch={() => undefined}
      onCancelPrefetch={() => undefined}
      reviewLinkShareIds={new Map([["source-a:folder-1", "share-1"]])}
      activeExternalSourceId="source-a"
      onCopyReviewLink={() => undefined}
      onRefreshReviewLink={() => undefined}
    />));

    const trigger = host.querySelector<HTMLButtonElement>(".folder-share-trigger");
    expect(trigger).not.toBeNull();
    expect(trigger?.getAttribute("aria-label")).toContain("Shared folder");

    await act(async () => trigger?.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    const menu = document.body.querySelector(".folder-share-menu");
    expect(menu?.textContent).toContain("Sao chép đường dẫn chia sẻ");
    expect(menu?.textContent).toContain("Cập nhật đường dẫn chia sẻ");

    await act(async () => root.unmount());
  });

  it("does not render a trigger for a different source identity", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<DriveTreeNode
      node={folder("folder-1", "Plain folder")}
      ancestors={[]}
      activePathIds={new Set()}
      childrenByParent={{ "folder-1": [] }}
      expanded={new Set()}
      loadingNodes={new Set()}
      onOpen={() => undefined}
      onToggle={() => undefined}
      onPrefetch={() => undefined}
      onCancelPrefetch={() => undefined}
      reviewLinkShareIds={new Map([["source-b:folder-1", "share-1"]])}
      activeExternalSourceId="source-a"
      onCopyReviewLink={() => undefined}
      onRefreshReviewLink={() => undefined}
    />));

    expect(host.querySelector(".folder-share-trigger")).toBeNull();
    await act(async () => root.unmount());
  });
});
