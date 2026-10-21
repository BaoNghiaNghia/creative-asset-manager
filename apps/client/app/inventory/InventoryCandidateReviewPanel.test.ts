import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { InventoryCandidateReviewPanel } from "./InventoryCandidateReviewPanel";

describe("Catinat candidate preview", () => {
  it("shows a read-only review surface without an automatic approval action", () => {
    const markup = renderToStaticMarkup(createElement(InventoryCandidateReviewPanel));
    expect(markup).toContain("Danh mục nguyên liệu cần đối chiếu");
    expect(markup).toContain("Chỉ nhóm có tên chính xác");
    expect(markup).not.toContain("Duyệt tất cả");
    expect(markup).not.toContain("Ký ca");
  });
});
