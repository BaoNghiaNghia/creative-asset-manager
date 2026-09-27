import { describe, expect, it } from "vitest";
import { routeForPath } from "../AppRoute";
import { scoutCommand } from "./RealisticReviewUgcPage";

describe("Realistic Review UGC route", () => {
  it("routes the dedicated top-level workspace", () => {
    expect(routeForPath("/realistic-review-ugc")).toBe("realistic-review-ugc");
    expect(routeForPath("/realistic-review-ugc/")).toBe("realistic-review-ugc");
  });

  it("builds a local scout command without changing the API host", () => {
    const command = scoutCommand("https://creative.example/", "campaign-1", "secret-token");
    expect(command).toContain("--base-url \"https://creative.example\"");
    expect(command).toContain("--campaign-id \"campaign-1\"");
    expect(command).toContain("--token \"secret-token\"");
    expect(command).toContain("--profile-dir");
  });
});
