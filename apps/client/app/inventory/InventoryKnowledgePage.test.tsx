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

import { InventoryKnowledgePage, isConflictingVerifiedObservation } from "./InventoryKnowledgePage";

it("does not offer direct activation for inconsistent verified observations", () => {
  expect(isConflictingVerifiedObservation({ structured_rule: { learner: "verified_daily_assessment_v2", consistent_conclusion: false } })).toBe(true);
  expect(isConflictingVerifiedObservation({ structured_rule: { learner: "verified_daily_assessment_v2", consistent_conclusion: true } })).toBe(false);
  expect(isConflictingVerifiedObservation({ structured_rule: { executable: false } })).toBe(false);
});

describe("InventoryKnowledgePage", () => {
  it("makes human approval explicit and separates Gemini proposals from active knowledge", () => {
    const markup = renderToStaticMarkup(<InventoryKnowledgePage />);
    expect(markup).toContain("Kiến thức kiểm kê");
    expect(markup).toContain("Gemini chỉ được đề xuất");
    expect(markup).toContain("Đang áp dụng");
    expect(markup).toContain("Gemini đề xuất");
    expect(markup).toContain("Bản nháp");
    expect(markup).toContain("Ngày có đối soát xác minh");
    expect(markup).toContain("Lần học gần nhất");
    expect(markup).toContain("Đề xuất mâu thuẫn");
    expect(markup).toContain("Lưu bản nháp");
  });
});
