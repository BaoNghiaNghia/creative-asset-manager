import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { routeForPath, workspaceRouteForApplicationRoute, requiredPermissionForApplicationRoute } from "../AppRoute";
import { TiktokShopScoutUgcPage, TIKTOKSHOP_SOURCE_ROOT } from "./TiktokShopScoutUgcPage";

describe("Tiktokshop workspace Phase 1 isolation", () => {
  it("has a distinct permission-gated route separate from GatorHats Reviews", () => {
    expect(routeForPath("/tiktokshop/scout-ugc")).toBe("tiktokshop-scout-ugc");
    expect(workspaceRouteForApplicationRoute("tiktokshop-scout-ugc")).toBe("tiktokshop");
    expect(requiredPermissionForApplicationRoute("tiktokshop-scout-ugc")).toBe("realistic_review_ugc.read");
    expect(routeForPath("/realistic-review-ugc")).toBe("realistic-review-ugc");
  });

  it("renders only the TiktokShop folder and Stage 3-5 while keeping generation gated", () => {
    const html = renderToStaticMarkup(<TiktokShopScoutUgcPage />);
    expect(html).toContain("Tiktokshop");
    expect(html).toContain("Scout &amp; UGC");
    expect(html).toContain(TIKTOKSHOP_SOURCE_ROOT);
    expect(html).toContain("Stage 3");
    expect(html).toContain("Stage 4");
    expect(html).toContain("Stage 5");
    expect(html).toContain("Generation is not enabled yet");
    expect(html).not.toContain("1kNBQU4O-i6cbDBnRrhPGNENHvieWYPfX");
  });
});
