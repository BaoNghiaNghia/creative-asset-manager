import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { Asset } from "../types";
import { AssetGrid } from "./AssetGrid";

function folder(id: string, name: string, sourceId: string): Asset {
  return {
    provider: "google-drive",
    id,
    name,
    kind: "folder",
    mime_type: "application/vnd.google-apps.folder",
    is_folder: true,
    external_source_id: sourceId,
  } as Asset;
}

describe("AssetGrid shared-folder actions", () => {
  it("renders the circular shared-link trigger only for folders with an active share", () => {
    const shared = folder("shared", "Shared folder", "source-a");
    const plain = folder("plain", "Plain folder", "source-a");
    const markup = renderToStaticMarkup(<AssetGrid
      items={[shared, plain]}
      path={[]}
      selected={new Set()}
      metadataByItem={{}}
      onOpen={() => undefined}
      onToggle={() => undefined}
      onReplaceSelection={() => undefined}
      onPrefetch={() => undefined}
      onCancelPrefetch={() => undefined}
      onPreview={() => undefined}
      onRate={() => undefined}
      onDetails={() => undefined}
      onFocus={() => undefined}
      onContextMenu={() => undefined}
      reviewLinkShareIds={new Map([["source-a:shared", "share-1"]])}
      onCopyReviewLink={() => undefined}
      onRefreshReviewLink={() => undefined}
    />);

    expect(markup).toContain('aria-label="Shared link actions for Shared folder"');
    expect(markup).not.toContain('aria-label="Shared link actions for Plain folder"');
    expect(markup).toContain("folder-share-trigger");
  });
});
