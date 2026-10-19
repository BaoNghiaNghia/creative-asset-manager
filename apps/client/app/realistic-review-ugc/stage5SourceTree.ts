import type { Stage3ReviewGroup, Stage3ReviewGroupList } from "./types";

/** Counts may be based on the full API folder inventory, independent of loaded pages. */
export type Stage5FolderNode = {
  key: string;
  name: string;
  path: string;
  depth: number;
  imageCount: number;
  groupIds: string[];
  children: Stage5FolderNode[];
};

function normalizedPath(path: string): string {
  return (path || "").replace(/\\/g, "/").split("/").map(part => part.trim()).filter(Boolean).join("/");
}

export function buildStage5FolderTree(
  groups: Stage3ReviewGroup[],
  folderCounts?: Stage3ReviewGroupList["folder_counts"],
): Stage5FolderNode {
  const root: Stage5FolderNode = {
    key: "all", name: "All folders", path: "", depth: -1,
    imageCount: 0, groupIds: [], children: [],
  };
  const index = new Map<string, Stage5FolderNode>();
  const entries = folderCounts !== undefined
    ? folderCounts.map(item => ({ path: item.path, id: item.folder_id, count: item.image_count }))
    : groups.map(group => ({
      path: group.folder_path || (group.folder_name === "Root folder" ? "" : group.folder_name),
      id: group.folder_id,
      count: group.images.length,
    }));

  for (const entry of entries) {
    const path = normalizedPath(entry.path);
    const segments = path ? path.split("/") : ["Root folder"];
    root.imageCount += entry.count;
    if (!root.groupIds.includes(entry.id)) root.groupIds.push(entry.id);
    let parent = root;
    for (let depth = 0; depth < segments.length; depth += 1) {
      const currentPath = path ? segments.slice(0, depth + 1).join("/") : "";
      const key = "folder:" + currentPath;
      let node = index.get(key);
      if (!node) {
        node = {
          key, name: segments[depth], path: currentPath, depth,
          imageCount: 0, groupIds: [], children: [],
        };
        index.set(key, node);
        parent.children.push(node);
      }
      node.imageCount += entry.count;
      if (!node.groupIds.includes(entry.id)) node.groupIds.push(entry.id);
      parent = node;
    }
  }
  function sort(nodes: Stage5FolderNode[]) {
    nodes.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
    nodes.forEach(node => sort(node.children));
  }
  sort(root.children);
  return root;
}

export function findStage5FolderNode(
  root: Stage5FolderNode, key: string | null,
): Stage5FolderNode | null {
  if (key === null || key === root.key) return root;
  const stack = [...root.children];
  while (stack.length) {
    const node = stack.pop()!;
    if (node.key === key) return node;
    stack.push(...node.children);
  }
  return null;
}

export function groupsForStage5Folder(
  groups: Stage3ReviewGroup[], root: Stage5FolderNode, selectedKey: string | null,
): Stage3ReviewGroup[] {
  const node = findStage5FolderNode(root, selectedKey);
  if (!node || node.key === root.key) return groups;
  const ids = new Set(node.groupIds);
  return groups.filter(group => ids.has(group.folder_id));
}
