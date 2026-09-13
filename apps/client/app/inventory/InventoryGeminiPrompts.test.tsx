import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("./api", () => ({
  inventoryDailySheetApi: { getPrompts: vi.fn(() => new Promise(() => undefined)) },
}));

import { InventoryGeminiPrompts } from "./InventoryGeminiPrompts";

describe("InventoryGeminiPrompts", () => {
  it("renders a dedicated operational workspace instead of configuration controls", () => {
    const markup = renderToStaticMarkup(<InventoryGeminiPrompts />);
    expect(markup).toContain("Hướng dẫn nghiệp vụ cho Gemini");
    expect(markup).toContain("Phạm vi an toàn vẫn được backend khóa");
    expect(markup).toContain('class="inventory-prompts-page"');
  });
});
