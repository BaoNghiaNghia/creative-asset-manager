import {describe,it,expect} from "vitest";
import {renderToStaticMarkup} from "react-dom/server";
import {BrandingDnaKnowledge} from "./BrandingDnaKnowledge";

describe("Shop Knowledge source UX",()=>{
  it("provides per-shop upload, human review and cited retrieval without auto-apply",()=>{
    const html=renderToStaticMarkup(<BrandingDnaKnowledge/>);
    expect(html).toContain("Shop Knowledge · RAG Library");
    expect(html).toContain('accept=".txt,.md,.csv,.json,.docx,.pdf"');
    expect(html).toContain("PDF");
    expect(html).toContain("40 MB/60 file");
    expect(html).toContain("Tìm trong tài liệu đã duyệt");
    expect(html).toContain("RAG không tự thay đổi Brand DNA");
    expect(html).not.toContain("Đã tạo DNA");
  });
});
