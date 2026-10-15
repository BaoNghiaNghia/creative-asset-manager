// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ResizableSourceLayout } from "./ResizableSourceLayout";
import { buildSourcePlanFolderTree } from "./sourcePlanFolderTree";
import { findStage5FolderNode } from "./stage5SourceTree";
import { Stage5SourceTree } from "./Stage5SourceTree";
import { SourcePlanTable } from "./SourcePlanTable";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
afterEach(() => {
  document.body.replaceChildren();
  document.body.classList.remove("rrugc-source-split-dragging");
  localStorage.clear();
});

describe("Reviews Stage 3/4/5 source tree layout", () => {
  it("builds the full nested tree from unpaginated server folder counts", () => {
    const root = buildSourcePlanFolderTree({
      folders: [
        { path: "Hat A", group_count: 2, image_count: 5 },
        { path: "Hat A/Details", group_count: 1, image_count: 3 },
        { path: "Hat AB", group_count: 4, image_count: 4 },
      ], total_groups: 7,
    });
    expect(root.imageCount).toBe(12);
    expect(findStage5FolderNode(root, "folder:Hat A")?.imageCount).toBe(8);
    expect(findStage5FolderNode(root, "folder:Hat A/Details")?.imageCount).toBe(3);
    expect(findStage5FolderNode(root, "folder:Hat AB")?.imageCount).toBe(4);
  });

  it("renders the splitter and supports keyboard resizing with a saved width", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<ResizableSourceLayout stage={5}
      tree={<div>Source tree</div>}><div>Cards</div></ResizableSourceLayout>));
    const splitter = host.querySelector<HTMLElement>('[role="separator"]')!;
    expect(splitter.getAttribute("aria-valuenow")).toBe("295");
    await act(async () => splitter.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true })));
    expect(splitter.getAttribute("aria-valuenow")).toBe("315");
    expect(localStorage.getItem("rrugc:source-tree-width:5")).toBe("315");
    // Pointer drag uses capture so rapid movement outside the grab handle
    // continues resizing until release.
    splitter.setPointerCapture = vi.fn();
    splitter.hasPointerCapture = vi.fn(() => true);
    splitter.releasePointerCapture = vi.fn();
    await act(async () => {
      splitter.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, button: 0, clientX: 100 }));
      splitter.dispatchEvent(new MouseEvent("pointermove", { bubbles: true, clientX: 155 }));
      splitter.dispatchEvent(new MouseEvent("pointerup", { bubbles: true, clientX: 155 }));
    });
    expect(splitter.getAttribute("aria-valuenow")).toBe("370");
    expect(document.body.classList.contains("rrugc-source-split-dragging")).toBe(false);
    await act(async () => root.unmount());
  });

  it("selects Stage 3 source folders and sorts Scout DESC then ASC", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const onSortByChange = vi.fn();
    const onSortDirectionChange = vi.fn();
    const onFolderSelect = vi.fn();
    const props = {
      plans: [], total: 0, page: 1, pageSize: 10, query: "", syncing: false, message: "",
      folderData: { folders: [{ path: "Hats", group_count: 2, image_count: 3 }], total_groups: 2 },
      onFolderSelect,
      onSync: vi.fn(), onPageChange: vi.fn(), onPageSizeChange: vi.fn(),
      onQueryChange: vi.fn(), onSortByChange, onSortDirectionChange,
    };
    await act(async () => root.render(<SourcePlanTable {...props} sortBy="source" sortDirection="asc" />));
    const tree = host.querySelector(".rrugc-stage5-source-tree")!;
    expect(tree.getAttribute("aria-label")).toBe("Stage 3 source folder tree");
    await act(async () => tree.querySelector<HTMLButtonElement>(".rrugc-stage5-tree-folder")!.click());
    expect(onFolderSelect).toHaveBeenCalledWith("Hats");
    await act(async () => host.querySelector<HTMLButtonElement>(".rrugc-stage3-scout-sort")!.click());
    expect(onSortByChange).toHaveBeenCalledWith("progress");
    expect(onSortDirectionChange).toHaveBeenCalledWith("desc");
    await act(async () => root.render(<SourcePlanTable {...props} sortBy="progress" sortDirection="desc" />));
    expect(host.querySelector("thead th[aria-sort='descending']")).not.toBeNull();
    await act(async () => host.querySelector<HTMLButtonElement>(".rrugc-stage3-scout-sort")!.click());
    expect(onSortDirectionChange).toHaveBeenLastCalledWith("asc");
    await act(async () => root.unmount());
  });

  it("reuses the same Stage 4 source navigation", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const tree = buildSourcePlanFolderTree({ folders: [{ path: "One", image_count: 2, group_count: 1 }], total_groups: 1 });
    await act(async () => root.render(<Stage5SourceTree stage={4} root={tree} selectedKey={null}
      onSelect={() => undefined} hasMore={false} />));
    expect(host.querySelector('[aria-label="Stage 4 source folder tree"]')).not.toBeNull();
    expect(host.textContent).toContain("Folders from Stage 3 sources");
    await act(async () => root.unmount());
  });
});
