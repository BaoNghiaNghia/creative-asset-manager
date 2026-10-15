import type { SourcePlanFolders } from "./api";
import type { Stage5FolderNode } from "./stage5SourceTree";

/** Folder counts are returned independently of the paginated Stage 3/4 tables. */
export function buildSourcePlanFolderTree(data: SourcePlanFolders | null): Stage5FolderNode {
  const root: Stage5FolderNode = {
    key: "all", name: "All folders", path: "", depth: -1,
    imageCount: 0, groupIds: [], children: [],
  };
  if (!data) return root;
  const index = new Map<string, Stage5FolderNode>();
  for (const item of data.folders) {
    const segments = item.path.replace(/\\/g, "/").split("/").filter(Boolean);
    if (!segments.length) segments.push("Root folder");
    let parent = root;
    segments.forEach((segment, depth) => {
      const path = item.path ? segments.slice(0, depth + 1).join("/") : "";
      const key = "folder:" + path;
      let node = index.get(key);
      if (!node) {
        node = { key, name: segment, path, depth, imageCount: 0, groupIds: [], children: [] };
        index.set(key, node);
        parent.children.push(node);
      }
      node.imageCount += item.image_count;
      parent = node;
    });
    root.imageCount += item.image_count;
  }
  const sort = (nodes: Stage5FolderNode[]) => {
    nodes.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
    nodes.forEach(node => sort(node.children));
  };
  sort(root.children);
  return root;
}
