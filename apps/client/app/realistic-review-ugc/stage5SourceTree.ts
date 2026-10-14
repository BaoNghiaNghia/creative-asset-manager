import type { Stage3ReviewGroup } from "./types";

/** All counts describe the currently loaded Stage 5 pages, not the full Drive. */
export type Stage5FolderNode = {
  key: string;
  name: string;
  path: string;
  depth: number;
  imageCount: number;
  groupIds: string[];
  children: Stage5FolderNode[];
};

function normalizedSegments(group: Stage3ReviewGroup): string[] {
  const path = (group.folder_path || "").replace(/\\/g, "/").split("/")
    .map(segment => segment.trim()).filter(Boolean);
  if (path.length) return path;
  return [group.folder_name?.trim() || "Root folder"];
}

export function buildStage5FolderTree(groups: Stage3ReviewGroup[]): Stage5FolderNode {
  const root: Stage5FolderNode = {
    key: "all", name: "All folders", path: "", depth: -1,
    imageCount: 0, groupIds: [], children: [],
  };
  const byKey = new Map<string, Stage5FolderNode>();
  for (const group of groups) {
    const count = group.images.length;
    root.imageCount += count;
    root.groupIds.push(group.folder_id);
    let parent = root;
    const segments = normalizedSegments(group);
    for (let index = 0; index < segments.length; index++) {
      const path = segments.slice(0, index + 1).join("/");
      const key = "folder:" + path;
      let node = byKey.get(key);
      if (!node) {
        node = {
          key, name: segments[index], path, depth: index,
          imageCount: 0, groupIds: [], children: [],
        };
        byKey.set(key, node);
        parent.children.push(node);
      }
      node.imageCount += count;
      node.groupIds.push(group.folder_id);
      parent = node;
    }
  }
  function sort(nodes: Stage5FolderNode[]) {
    nodes.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
    for (const node of nodes) sort(node.children);
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
