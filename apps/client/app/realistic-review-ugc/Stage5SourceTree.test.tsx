// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Stage3ReviewGroups } from "./Stage3ReviewGroups";
import { buildStage5FolderTree, findStage5FolderNode, groupsForStage5Folder } from "./stage5SourceTree";
import type { Stage3ReviewGroup, Stage3ReviewGroupList, Stage3ReviewImage } from "./types";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
afterEach(() => document.body.replaceChildren());

function group(folderId: string, folderPath: string, count: number): Stage3ReviewGroup {
  const images = Array.from({ length: count }, (_, index) => ({
    stage2_job_id: folderId + "-image-" + index,
    source_name: folderId + "-image-" + index,
    analysis_status: "pending",
    preview_url: "/qa/" + folderId + "/" + index,
    final_score: null,
    summary: "Awaiting analysis",
  } as Stage3ReviewImage));
  return {
    folder_id: folderId,
    folder_name: folderPath.split("/").at(-1) || "Root folder",
    folder_path: folderPath,
    image_count: count,
    status: "pending",
    ready_count: 0,
    rejected_count: 0,
    analyzing_count: 0,
    pending_count: count,
    error_count: 0,
    latest_completed_at: null,
    images,
  };
}

const groups = [
  group("hat-a", "QA/Hat A", 1),
  group("hat-b", "QA/Hat B", 2),
  group("hat-a-extra", "QA/Hat A Extra", 1),
  group("other", "Other", 1),
];

describe("Stage 5 source folder tree", () => {
  it("builds nested folder paths, keeps sibling prefixes separate and counts loaded images", () => {
    const tree = buildStage5FolderTree(groups);
    expect(tree.imageCount).toBe(5);
    expect(tree.children.map(item => item.name)).toEqual(["Other", "QA"]);
    expect(findStage5FolderNode(tree, "folder:QA")?.imageCount).toBe(4);
    expect(findStage5FolderNode(tree, "folder:QA/Hat A")?.imageCount).toBe(1);
    expect(groupsForStage5Folder(groups, tree, "folder:QA").map(item => item.folder_id))
      .toEqual(["hat-a", "hat-b", "hat-a-extra"]);
    expect(groupsForStage5Folder(groups, tree, "folder:QA/Hat A").map(item => item.folder_id))
      .toEqual(["hat-a"]);
    expect(groupsForStage5Folder(groups, tree, null)).toHaveLength(4);
    expect(groupsForStage5Folder(groups, tree, "folder:deleted-folder")).toHaveLength(4);
    const windows = buildStage5FolderTree([group("slash", "Hats\\Trucker", 1)]);
    expect(findStage5FolderNode(windows, "folder:Hats/Trucker")?.groupIds).toEqual(["slash"]);
  });

  it("selects a parent, opens a child folder, and returns to all images without losing cards", async () => {
    const data: Stage3ReviewGroupList = {
      items: groups, total_groups: groups.length, total_images: 5,
      has_more: true, next_cursor: "next",
      ready_images: 0, rejected_images: 0, analyzing_images: 0,
      pending_images: 5, error_images: 0,
    };
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    const onAnalyze = vi.fn();
    await act(async () => root.render(<Stage3ReviewGroups
      data={data} loading={false} analyzing={false} message=""
      onAnalyze={onAnalyze} onLoadMore={() => undefined}
    />));
    const tree = host.querySelector<HTMLElement>(".rrugc-stage5-source-tree");
    const content = host.querySelector<HTMLElement>(".rrugc-stage5-folder-content");
    expect(tree).not.toBeNull();
    // Global mobile workspace styles hide <aside>; the folder rail must not use it.
    expect(tree?.tagName).toBe("DIV");
    expect(tree?.getAttribute("role")).toBe("complementary");
    expect(tree?.querySelector('[role="navigation"]')?.tagName).toBe("DIV");
    expect(tree?.querySelector("nav")).toBeNull();
    expect(content).not.toBeNull();
    const toolbar = host.querySelector(".rrugc-stage5-toolbar");
    expect(toolbar?.querySelector(".rrugc-stage5-search-group")).not.toBeNull();
    expect(toolbar?.querySelector(".rrugc-stage5-sort-bar")).not.toBeNull();
    expect(toolbar?.querySelector(".rrugc-stage5-toolbar-actions")).not.toBeNull();
    expect(toolbar?.querySelectorAll(".rrugc-stage5-toolbar-stats span")).toHaveLength(3);
    expect(host.querySelector(".rrugc-stage4-heading .rrugc-stage3-stats")).toBeNull();
    expect(host.querySelector(".rrugc-stage5-folder-summary")).toBeNull();
    const cards = () => content!.querySelectorAll(".rrugc-stage3-image");
    expect(cards()).toHaveLength(5);

    const folderButton = (label: string) => [...tree!.querySelectorAll<HTMLButtonElement>(
      ".rrugc-stage5-tree-folder",
    )].find(button => button.querySelector(".rrugc-stage5-tree-folder-name")?.textContent === label)!;
    await act(async () => folderButton("QA").click());
    expect(cards()).toHaveLength(4);
    expect(folderButton("QA").getAttribute("aria-pressed")).toBe("true");
    await act(async () => folderButton("Hat A").click());
    expect(cards()).toHaveLength(1);
    expect(content!.textContent).toContain("Hat A");
    await act(async () => tree!.querySelector<HTMLButtonElement>(".rrugc-stage5-tree-all")!.click());
    expect(cards()).toHaveLength(5);
    expect(tree!.querySelector(".rrugc-stage5-tree-all")?.getAttribute("aria-pressed")).toBe("true");
    expect(host.querySelector(".rrugc-stage3-load-more")).not.toBeNull();
    expect(onAnalyze).not.toHaveBeenCalled();
    await act(async () => root.unmount());
  });
});
