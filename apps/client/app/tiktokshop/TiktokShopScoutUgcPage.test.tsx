import { describe, expect, it } from "vitest";
import tiktokNavIcon from "../../assets/navigation/tiktokshop.png";
import { renderToStaticMarkup } from "react-dom/server";
import { routeForPath, workspaceRouteForApplicationRoute, requiredPermissionForApplicationRoute } from "../AppRoute";
import { TiktokShopScoutUgcPage, TIKTOKSHOP_SOURCE_ROOT } from "./TiktokShopScoutUgcPage";

describe("Tiktokshop workspace Phase 3 isolation", () => {
  it("has a distinct permission-gated route separate from GatorHats Reviews", () => {
    expect(routeForPath("/tiktokshop/scout-ugc")).toBe("tiktokshop-scout-ugc");
    expect(workspaceRouteForApplicationRoute("tiktokshop-scout-ugc")).toBe("tiktokshop");
    expect(requiredPermissionForApplicationRoute("tiktokshop-scout-ugc")).toBe("realistic_review_ugc.read");
    expect(routeForPath("/realistic-review-ugc")).toBe("realistic-review-ugc");
  });

  it("renders only the TiktokShop folder and isolated Stage 3–5 creative workflows", () => {
    const html = renderToStaticMarkup(<TiktokShopScoutUgcPage />);
    expect(html).toContain("Tiktokshop");
    expect(html).toContain("Scout &amp; UGC");
    expect(html).toContain(TIKTOKSHOP_SOURCE_ROOT);
    expect(html).toContain("rrugc-tiktokshop-workspace");
    expect(html).toContain("TiktokShop Drive");
    expect(html).toContain('aria-haspopup="dialog"');
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain("Settings");
    expect(html).not.toContain("Windows · TikTokShop Scout");
    expect(html).not.toContain("Source photos are indexed automatically");
    expect(html).toContain("Stage 3");
    expect(html).toContain("Stage 4");
    expect(html).toContain("Stage 5");
    expect(html).toContain("tiktokshop-apparel-ugc-v1");
    expect(html).toContain("Internal apparel image QA only");
    expect(html).toContain("Export internal QA CSV");
    expect(html).not.toContain("Generation is not enabled yet");
    expect(html).not.toContain("1kNBQU4O-i6cbDBnRrhPGNENHvieWYPfX");
  });
  it("uses the supplied TikTokShop logo artwork in the navigation", () => {
    expect(tiktokNavIcon).toContain("tiktokshop.png");
  });
});