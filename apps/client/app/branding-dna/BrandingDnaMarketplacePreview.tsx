import {useEffect, useRef, useState} from "react";
import "./BrandingDnaMarketplacePreview.css";

type PreviewProduct = {
  listing_key: string;
  identity_quality?: string;
  title: string;
  category: string | null;
  variant_count: number;
  image_count: number;
  warnings: string[];
};
type PreviewReport = {
  adapter_version: string;
  product_count: number;
  row_count: number;
  issue_count: number;
  issues: {row: number; code: string}[];
  source_sha256: string;
  items: PreviewProduct[];
  source_identity_verified: boolean;
  rights_verified: boolean;
  evidence_committed: boolean;
  scan_enabled: boolean;
  blockers: string[];
};

export function BrandingDnaMarketplacePreview({shopId, platform}: {shopId: string; platform: "amazon" | "etsy"}) {
  const [report, setReport] = useState<PreviewReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const current = useRef(0);
  const activeAbort = useRef<AbortController | null>(null);

  useEffect(() => {
    current.current += 1;
    activeAbort.current?.abort();
    setReport(null);
    setBusy(false);
    setError("");
    return () => {
      current.current += 1;
      activeAbort.current?.abort();
    };
  }, [shopId, platform]);

  async function previewFile(file: File | undefined) {
    if (!file) return;
    const request = ++current.current;
    activeAbort.current?.abort();
    setReport(null);
    setError("");
    if (!/\.(csv|tsv|txt)$/i.test(file.name) || file.size > 2_000_000 || file.size === 0) {
      setError("Chỉ hỗ trợ CSV/TSV/TXT, dung lượng không quá 2 MB.");
      return;
    }
    setBusy(true);
    const controller = new AbortController();
    activeAbort.current = controller;
    try {
      const csv = await file.text();
      if (request !== current.current) return;
      const response = await fetch(
        "/api/v1/branding-dna/shops/" + encodeURIComponent(shopId) + "/source-preview", {
          method: "POST",
          credentials: "same-origin",
          cache: "no-store",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({format: platform === "etsy" ? "etsy_listings_csv" : "amazon_inventory_report", csv_text: csv}),
          signal: controller.signal,
        },
      );
      const data = await response.json() as PreviewReport | {detail?: unknown};
      if (request !== current.current) return;
      if (!response.ok) {
        const detail = "detail" in data && typeof data.detail === "string" ? data.detail : String(response.status);
        throw new Error("File không đạt kiểm tra: " + detail);
      }
      const preview = data as PreviewReport;
      if (preview.scan_enabled !== false || preview.rights_verified !== false ||
          preview.source_identity_verified !== false || preview.evidence_committed !== false) {
        throw new Error("Trạng thái nguồn bất thường — đã từ chối kết quả.");
      }
      setReport(preview);
    } catch (reason) {
      if (request === current.current && !controller.signal.aborted) {
        setError(reason instanceof Error ? reason.message : "Không thể kiểm tra bản export.");
      }
    } finally {
      if (request === current.current) setBusy(false);
    }
  }

  return <section className="bdna-source-preview" aria-label="Amazon Etsy source preview">
    <div className="bdna-source-preview-header">
      <div>
        <h4>{platform === "etsy" ? "Etsy Listings CSV" : "Amazon Inventory CSV/TSV"} · Kiểm tra nguồn</h4>
        <p>Đọc trước bản export sản phẩm. Không quét website, không lưu ảnh hoặc tạo job.</p>
      </div>
      <label>
        Chọn tệp {platform === "etsy" ? "Etsy CSV" : "Amazon CSV/TSV"} (tối đa 2 MB)
        <input type="file" accept=".csv,.tsv,.txt,text/csv,text/tab-separated-values" disabled={busy}
          aria-label="Chọn Amazon hoặc Etsy export để xem trước"
          onChange={e => {
            const file = e.currentTarget.files?.[0];
            e.currentTarget.value = "";
            void previewFile(file);
          }}/>
      </label>
    </div>
    {busy && <p role="status">Đang phân tích file CSV…</p>}
    {error && <p className="bdna-source-preview-error" role="alert">{error}</p>}
    {report && <div className="bdna-source-preview-report">
      <div className="bdna-source-preview-metrics">
        <span><strong>{report.product_count}</strong> sản phẩm trong file</span>
        <span><strong>{report.row_count}</strong> dòng</span>
        <span><strong>{report.issue_count}</strong> vấn đề</span>
      </div>
      <p className="bdna-source-preview-warning">
        Nguồn chưa được đối chiếu quyền sở hữu, URL ảnh chưa được tải hoặc xác minh. Không có dữ liệu nào được ghi vào Product Explorer.
      </p>
      <div className="bdna-source-preview-list">
        {report.items.slice(0, 30).map(item =>
          <div key={item.listing_key} className="bdna-source-preview-item">
            <strong>{item.title || "(Thiếu tên trong export)"}</strong>
            <span>{item.listing_key} · {item.variant_count} SKU · {item.image_count} URL ảnh · {item.identity_quality === "provisional_not_canonical" ? "ID tạm" : "ID chưa xác minh"}</span>
            {item.warnings.length > 0 && <small>Cảnh báo: {item.warnings.join(", ")}</small>}
          </div>
        )}
      </div>
      {report.product_count > 30 && <p>Chỉ hiển thị 30 sản phẩm đầu tiên trong bản xem trước.</p>}
      {report.issues.length > 0 && <p>Các lỗi đầu tiên: {report.issues.slice(0, 5).map(
        issue => issue.code + " (dòng " + issue.row + ")",
      ).join("; ")}</p>}
    </div>}
  </section>;
}
