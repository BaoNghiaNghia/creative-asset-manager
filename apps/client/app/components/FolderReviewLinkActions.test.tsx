import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { Asset } from "../types";
import {
  FolderReviewLinkActions,
  reviewShareIdForFolder,
} from "./FolderReviewLinkActions";

const folder: Asset = {
  provider: "google-drive",
  id: "folder-1",
  name: "Desify - Image & Video Assets",
  kind: "folder",
  mime_type: "application/vnd.google-apps.folder",
  external_source_id: "source-a",
};

describe("FolderReviewLinkActions", () => {
  it("resolves the current folder share from the folder source or active source", () => {
    const shares = new Map([["source-a:folder-1", "share-1"]]);
    expect(reviewShareIdForFolder(folder, null, shares)).toBe("share-1");
    expect(
      reviewShareIdForFolder(
        { ...folder, external_source_id: undefined },
        "source-a",
        shares,
      ),
    ).toBe("share-1");
    expect(
      reviewShareIdForFolder(
        { ...folder, kind: "image" },
        "source-a",
        shares,
      ),
    ).toBeNull();
  });

  it("renders the three-dot shared-link trigger beside a folder title", () => {
    const markup = renderToStaticMarkup(
      createElement(FolderReviewLinkActions, {
        item: folder,
        shareId: "share-1",
        onCopyReviewLink: () => undefined,
        onRefreshReviewLink: () => undefined,
        titleContext: true,
      }),
    );
    expect(markup).toContain("folder-title-share-trigger");
    expect(markup).toContain(
      'aria-label="Shared link actions for Desify - Image &amp; Video Assets"',
    );
    expect(markup).toContain('aria-haspopup="menu"');
    expect((markup.match(/<circle/g) || []).length).toBe(3);
  });
});
