import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("./api", () => ({
  inventoryKnowledgeApi: {
    list: vi.fn(() => new Promise(() => undefined)),
    create: vi.fn(),
    revise: vi.fn(),
    activate: vi.fn(),
    reject: vi.fn(),
  },
}));

import { InventoryKnowledgePage } from "./InventoryKnowledgePage";

describe("InventoryKnowledgePage", () => {
  it("makes human approval explicit and separates Gemini proposals from active knowledge", () => {
    const markup = renderToStaticMarkup(<InventoryKnowledgePage />);
    expect(markup).toContain("Kiến thức kiểm kê");
    expect(markup).toContain("Gemini chỉ được đề xuất");
    expect(markup).toContain("Đang áp dụng");
    expect(markup).toContain("Gemini đề xuất");
    expect(markup).toContain("Bản nháp");
    expect(markup).toContain("Lưu bản nháp");
  });
});
