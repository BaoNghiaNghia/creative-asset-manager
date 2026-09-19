import { afterEach, describe, expect, it, vi } from "vitest";
import { reviewShareUrl } from "./PublicReviewRoute";

describe("reviewShareUrl", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("keeps the bearer key in the fragment and encodes it", () => {
    vi.stubGlobal("location", { origin: "https://creative-assets.example" });
    expect(reviewShareUrl("share-id", "key+/=?")).toBe(
      "https://creative-assets.example/share/share-id#key=key%2B%2F%3D%3F",
    );
  });
});
