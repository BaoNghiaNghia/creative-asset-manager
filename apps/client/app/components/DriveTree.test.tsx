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

describe("DriveTreeNode review-link actions", () => {
  it("renders the copy action only for the exact shared folder, not its children", () => {
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
      reviewLinkShareIds={new Map([[root.id, "share-root"]])}
      onCopyReviewLink={() => undefined}
    />);

    expect(markup).toContain('aria-label="Copy a new secure review link for Shared root"');
    expect(markup).not.toContain('aria-label="Copy a new secure review link for Child folder"');
  });
});
