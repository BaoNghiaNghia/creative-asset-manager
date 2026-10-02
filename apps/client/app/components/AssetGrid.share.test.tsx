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
  it("shows type/date in the metadata row and does not render star ratings", () => {
    const image = {
      provider: "google-drive",
      id: "image-1",
      name: "sample.jpg",
      kind: "image",
      mime_type: "image/jpeg",
      is_folder: false,
      modified_at: "2026-10-01T00:00:00Z",
      external_source_id: "source-a",
    } as Asset;

    const markup = renderToStaticMarkup(<AssetGrid
      items={[image]}
      path={[]}
      selected={new Set()}
      metadataByItem={{
        "image-1": {
          item_id: "image-1",
          tag_ids: [],
          rating: 5,
          processing_status: "discovered",
        },
      }}
      onOpen={() => undefined}
      onReplaceSelection={() => undefined}
      onPrefetch={() => undefined}
      onCancelPrefetch={() => undefined}
      onPreview={() => undefined}
      onDetails={() => undefined}
      onFocus={() => undefined}
      onContextMenu={() => undefined}
    />);

    expect(markup).toContain("processing-status");
    expect(markup).toContain("asset-type-date");
    expect(markup).not.toContain("asset-rating");
    expect(markup).not.toContain("★");
    expect(markup).not.toContain('class="check"');
  });

  it("renders the circular three-dot trigger only for folders with an active share", () => {
    const shared = folder("shared", "Shared folder", "source-a");
    const plain = folder("plain", "Plain folder", "source-a");
    const markup = renderToStaticMarkup(<AssetGrid
      items={[shared, plain]}
      path={[]}
      selected={new Set()}
      metadataByItem={{}}
      onOpen={() => undefined}
      onReplaceSelection={() => undefined}
      onPrefetch={() => undefined}
      onCancelPrefetch={() => undefined}
      onPreview={() => undefined}
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
    expect(markup).toContain("<circle");
  });

  it("uses the active Explorer source when a folder card omits external_source_id", () => {
    const shared = folder("shared", "Shared folder", "source-a");
    delete shared.external_source_id;

    const markup = renderToStaticMarkup(<AssetGrid
      items={[shared]}
      path={[]}
      selected={new Set()}
      metadataByItem={{}}
      onOpen={() => undefined}
      onReplaceSelection={() => undefined}
      onPrefetch={() => undefined}
      onCancelPrefetch={() => undefined}
      onPreview={() => undefined}
      onDetails={() => undefined}
      onFocus={() => undefined}
      onContextMenu={() => undefined}
      reviewLinkShareIds={new Map([["source-a:shared", "share-1"]])}
      activeExternalSourceId="source-a"
      onCopyReviewLink={() => undefined}
      onRefreshReviewLink={() => undefined}
    />);

    expect(markup).toContain('aria-label="Shared link actions for Shared folder"');
    expect(markup).toContain("folder-share-trigger");
  });
});
