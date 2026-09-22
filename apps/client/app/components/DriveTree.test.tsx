import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { Asset } from "../types";
import { DriveTreeNode } from "./DriveTree";

const folder = (id: string, name: string): Asset => ({
  provider: "google-drive",
  id,
  name,
  kind: "folder",
  mime_type: "application/vnd.google-apps.folder",
  is_folder: true,
} as Asset);

describe("DriveTreeNode", () => {
  it("renders folder navigation without shared-link actions", () => {
    const root = folder("shared-root", "Shared root");
    const child = folder("child-folder", "Child folder");
    const markup = renderToStaticMarkup(<DriveTreeNode
      node={root}
      ancestors={[]}
      activePathIds={new Set()}
      childrenByParent={{ [root.id]: [child] }}
      expanded={new Set([root.id])}
      loadingNodes={new Set()}
      onOpen={() => undefined}
      onToggle={() => undefined}
      onPrefetch={() => undefined}
      onCancelPrefetch={() => undefined}
    />);

    expect(markup).toContain("Shared root");
    expect(markup).toContain("Child folder");
    expect(markup).not.toContain("tree-review-link");
    expect(markup).not.toContain("Shared link actions");
  });
});
