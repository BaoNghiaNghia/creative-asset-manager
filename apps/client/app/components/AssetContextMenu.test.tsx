import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { Asset } from "../types";
import { AssetContextMenu, clampContextMenuPosition } from "./AssetContextMenu";

const item: Asset = {
  provider: "google-drive",
  id: "asset-1",
  name: "creative.jpg",
  kind: "image",
  mime_type: "image/jpeg",
  external_source_id: "source-1",
};

describe("AssetContextMenu", () => {
  it("keeps the menu inside the viewport", () => {
    expect(clampContextMenuPosition({ x: 990, y: 790 }, 300, 360, 1000, 800))
      .toEqual({ x: 692, y: 432 });
    expect(clampContextMenuPosition({ x: -20, y: -10 }, 300, 360, 1000, 800))
      .toEqual({ x: 8, y: 8 });
  });

  it("renders the expected file actions", () => {
    const noop = () => undefined;
    const markup = renderToStaticMarkup(<AssetContextMenu
      item={item}
      position={{ x: 10, y: 10 }}
      onOpen={noop}
      onDownload={noop}
      onCopy={noop}
      onRename={noop}
      onMove={noop}
      onDetails={noop}
      onDelete={noop}
      onClose={noop}
    />);
    expect(markup).toContain("Open preview");
    expect(markup).toContain("Download");
    expect(markup).toContain("Make a copy");
    expect(markup).toContain("Rename");
    expect(markup).toContain("Move to");
    expect(markup).toContain("File information");
    expect(markup).toContain("Move to trash");
  });
});
