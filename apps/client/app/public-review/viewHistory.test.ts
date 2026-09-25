// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";
import type { Asset } from "./api";
import {
  REVIEW_HISTORY_MAX_ITEMS,
  REVIEW_HISTORY_TTL_MS,
  readReviewHistory,
  recordReviewHistory,
} from "./viewHistory";

function asset(index: number): Asset {
  return {
    kind: "asset",
    asset_id: "asset-" + index,
    source_asset_id: "source-" + index,
    filename: "asset-" + index + ".jpg",
    media_type: "image/jpeg",
    thumbnail_url: "/thumb/" + index,
    preview_url: "/preview/" + index,
  };
}

describe("public review device-local history", () => {
  beforeEach(() => localStorage.clear());

  it("keeps histories isolated by public share id", () => {
    recordReviewHistory("share-a", asset(1), "folder-a", 1_000_000);
    recordReviewHistory("share-b", asset(2), "folder-b", 1_000_000);
    expect(readReviewHistory("share-a", 1_000_000).map(item => item.asset_id)).toEqual(["asset-1"]);
    expect(readReviewHistory("share-b", 1_000_000).map(item => item.asset_id)).toEqual(["asset-2"]);
  });

  it("deduplicates reopened assets and refreshes their position and timestamp", () => {
    recordReviewHistory("share-a", asset(1), "folder-a", 1_000_000);
    recordReviewHistory("share-a", asset(2), "folder-a", 1_001_000);
    const updated = recordReviewHistory("share-a", { ...asset(1), filename: "renamed.jpg" }, "folder-b", 1_002_000);
    expect(updated).toHaveLength(2);
    expect(updated[0]).toMatchObject({ asset_id: "asset-1", filename: "renamed.jpg", folder_id: "folder-b", viewed_at: 1_002_000 });
  });

  it("prunes entries older than 15 days", () => {
    const openedAt = 2_000_000;
    recordReviewHistory("share-a", asset(1), "folder-a", openedAt);
    expect(readReviewHistory("share-a", openedAt + REVIEW_HISTORY_TTL_MS - 1)).toHaveLength(1);
    expect(readReviewHistory("share-a", openedAt + REVIEW_HISTORY_TTL_MS + 1)).toHaveLength(0);
  });

  it("keeps only the 100 most recently viewed assets", () => {
    const start = 5_000_000;
    for (let index = 0; index < 105; index += 1) recordReviewHistory("share-a", asset(index), "folder-a", start + index);
    const history = readReviewHistory("share-a", start + 105);
    expect(history).toHaveLength(REVIEW_HISTORY_MAX_ITEMS);
    expect(history[0].asset_id).toBe("asset-104");
    expect(history.at(-1)?.asset_id).toBe("asset-5");
  });
});
