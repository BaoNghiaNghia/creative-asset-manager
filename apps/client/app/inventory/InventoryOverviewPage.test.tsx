import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { InventoryOverviewPage } from "./InventoryOverviewPage";
import { routeForPath, requiredPermissionForApplicationRoute, workspaceRouteForApplicationRoute } from "../AppRoute";

describe("Inventory workspace relocation", () => {
  it("opens existing daily Inventory overview at /inventory using the common navbar header", () => {
    const html = renderToStaticMarkup(<InventoryOverviewPage />);
    expect(html).toContain('class="inventory-workspace"');
    expect(html).toContain('aria-label="Inventory Daily"');
    expect(html).toContain("Đang tải dữ liệu Inventory hằng ngày");
    expect(html).toContain("Back to assets");
    expect(routeForPath("/inventory")).toBe("inventory");
    expect(workspaceRouteForApplicationRoute("inventory")).toBe("inventory");
    expect(requiredPermissionForApplicationRoute("inventory")).toBe("inventory.read");
  });

  it("keeps existing Inventory deep links resolvable", () => {
    for (const path of ["/inventory/materials", "/inventory/pipeline", "/inventory/settings"]) {
      expect(routeForPath(path)).toBe("inventory");
    }
  });
});
