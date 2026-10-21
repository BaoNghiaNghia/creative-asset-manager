import { useEffect, useMemo, useState } from "react";
import {
  catinatMaterialGroupsApi,
  type CatinatMaterialGroupsResponse,
} from "./api";

export function InventoryCandidateReviewPanel() {
  const [report, setReport] = useState<CatinatMaterialGroupsResponse | null>(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("all");
  const [loading, setLoading] = useState(false);
  const refresh = async () => {
    setLoading(true);
    setError("");
    try {
      setReport(await catinatMaterialGroupsApi.list());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Không thể tải danh mục");
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { void refresh(); }, []);
  const groups = useMemo(
    () => (report?.groups || []).filter(item => filter === "all" || item.status === filter),
    [report, filter],
  );
  const readyCount = report?.groups.filter(
    item => item.status === "exact_name_and_evidence_unit_candidate",
  ).length || 0;

  return <section className="inventory-pipeline">
    <details>
      <summary><strong>Danh mục nguyên liệu cần đối chiếu</strong> — {report ? `${report.unique_names} tên / ${report.source_rows} dòng nguồn` : "Đang tải…"}</summary>
      <p>Danh sách chỉ là đề xuất. Mapping, đơn vị quy đổi và số tồn chưa được chứng nhận; không có thao tác tự phê duyệt.</p>
      <div className="inventory-pipeline-header-actions">
        <label>Hiển thị{" "}
          <select value={filter} onChange={event => setFilter(event.target.value)}>
            <option value="all">Tất cả</option>
            <option value="exact_name_and_evidence_unit_candidate">Khớp chính xác (chờ chứng nhận)</option>
            <option value="material_review_required">Thiếu vật tư chuẩn</option>
            <option value="unit_review_required">Cần kiểm tra đơn vị</option>
            <option value="ambiguous_name">Tên chưa rõ</option>
          </select>
        </label>
        <button type="button" onClick={() => void refresh()} disabled={loading}>
          {loading ? "Đang tải…" : "Làm mới"}
        </button>
      </div>
      {error ? <p role="alert">{error}</p> : null}
      {report ? <p>{readyCount} tên có đề xuất khớp; {report.unique_names - readyCount} tên cần xác minh thêm.</p> : null}
      <div className="inventory-pipeline-scroll">
        <table>
          <thead><tr><th>NGUYÊN LIỆU</th><th>DÒNG NGUỒN</th><th>ĐƠN VỊ ĐƯỢC GHI NHẬN</th><th>TRẠNG THÁI</th></tr></thead>
          <tbody>{groups.map((group,index) => <tr key={group.name+"-"+index}>
            <td><strong>{group.name}</strong>{group.aliases.length > 1 ? <small> Tên khác: {group.aliases.slice(1).join(", ")}</small> : null}</td>
            <td>{group.source_row_count}</td>
            <td>{group.evidence_units.join(", ") || "Chưa có bằng chứng"}</td>
            <td>{group.status === "exact_name_and_evidence_unit_candidate" ? "Đề xuất khớp — chưa duyệt" :
              group.status === "unit_review_required" ? "Cần xác minh đơn vị" :
              group.status === "ambiguous_name" ? "Tên trùng chưa rõ" : "Cần bổ sung danh mục"}</td>
          </tr>)}</tbody>
        </table>
      </div>
    </details>
  </section>;
}
