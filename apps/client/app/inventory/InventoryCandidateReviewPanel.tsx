import { useCallback, useEffect, useMemo, useState } from "react";
import {
  catinatMaterialGroupsApi,
  type CatinatCandidateApprovalGroup,
  type CatinatMaterialGroupsResponse,
} from "./api";

const statusLabel: Record<string, string> = {
  exact_name_and_evidence_unit_candidate: "Tên và đơn vị khớp — chờ quản lý duyệt",
  unit_review_required: "Cần xác minh đơn vị",
  ambiguous_name: "Tên có nhiều kết quả",
  material_review_required: "Cần xác nhận vật tư chuẩn",
};
const blockerLabel: Record<string, string> = {
  candidate_group_too_large: "Nhóm quá lớn, cần tách để rà soát",
  duplicate_catalog_name: "Có nhiều vật tư chuẩn trùng tên",
  no_unique_catalog_match: "Chưa có một vật tư chuẩn duy nhất",
  candidate_name_variant_requires_review: "Tên nguồn khác tên chuẩn; cần quản lý xác nhận",
  conflicting_catalog_suggestion: "Có gợi ý mapping xung đột",
  missing_unit_evidence: "Thiếu bằng chứng đơn vị",
  incomplete_unit_evidence_reference: "Thiếu ô nguồn hoặc hash bằng chứng",
  unit_not_in_approved_item_units: "Đơn vị chưa có trong hồ sơ vật tư đã duyệt",
};

export function InventoryCandidateReviewPanel() {
  const [report, setReport] = useState<CatinatMaterialGroupsResponse | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [filter, setFilter] = useState("all");
  const [loading, setLoading] = useState(false);
  const [approving, setApproving] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setReport(await catinatMaterialGroupsApi.list());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Không thể tải danh mục");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const groups = useMemo(
    () => (report?.groups || []).filter(item => filter === "all" || item.status === filter),
    [report, filter],
  );
  const approvalGroups = useMemo(
    () => (report?.groups || []).flatMap(item => item.approval_groups || []),
    [report],
  );
  const actionable = useMemo(
    () => approvalGroups.filter(item => item.can_bulk_approve),
    [approvalGroups],
  );
  const selectedGroups = useMemo(
    () => actionable.filter(item => selected.includes(item.candidate_id)),
    [actionable, selected],
  );
  const readyCount = report?.groups.filter(
    item => item.status === "exact_name_and_evidence_unit_candidate",
  ).length || 0;

  const approveSelected = useCallback(async () => {
    if (!selectedGroups.length || approving) return;
    setApproving(true);
    setError("");
    setNotice("");
    try {
      const result = await catinatMaterialGroupsApi.bulkApprove({
        groups: selectedGroups.map(item => ({
          candidate_id: item.candidate_id,
          item_id: item.item_id as string,
          expected_count: item.expected_count,
          expected_candidate_ids: item.candidate_ids,
        })),
      });
      setSelected([]);
      setNotice(
        `Đã duyệt ${result.approved_groups} nhóm / ${result.approved_candidates} dòng ứng viên. Không ghi số tồn và không ghi Google Sheets.`,
      );
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Không thể duyệt nhóm nguyên liệu");
    } finally {
      setApproving(false);
    }
  }, [selectedGroups, approving, refresh]);

  return <section className="inventory-pipeline">
    <details>
      <summary><strong>Danh mục nguyên liệu cần đối chiếu</strong> — {report ? `${report.unique_names} tên / ${report.source_rows} dòng nguồn` : "Đang tải…"}</summary>
      <p>Đề xuất không đồng nghĩa với chứng nhận. Chỉ nhóm có tên chính xác, đơn vị có bằng chứng theo ô Sheets và không có xung đột mới được chọn duyệt. Không duyệt quy đổi bao bì, số tồn hoặc ký ca tại đây.</p>
      <div className="inventory-pipeline-header-actions">
        <label>Hiển thị{" "}
          <select value={filter} onChange={event => setFilter(event.target.value)}>
            <option value="all">Tất cả</option>
            <option value="exact_name_and_evidence_unit_candidate">Tên và đơn vị khớp</option>
            <option value="material_review_required">Cần xác nhận vật tư</option>
            <option value="unit_review_required">Cần kiểm tra đơn vị</option>
            <option value="ambiguous_name">Tên có nhiều kết quả</option>
          </select>
        </label>
        <button type="button" onClick={() => void refresh()} disabled={loading || approving}>
          {loading ? "Đang tải…" : "Làm mới"}
        </button>
      </div>
      {error ? <p className="inventory-error" role="alert">{error}</p> : null}
      {notice ? <p role="status">{notice}</p> : null}
      {report ? <p>{readyCount} tên khớp cả tên và đơn vị; {actionable.length} nhóm có đủ bằng chứng để quản lý duyệt. {report.unique_names - readyCount} tên cần xác minh thêm.</p> : null}
      <div className="inventory-pipeline-header-actions">
        <button type="button" className="secondary" disabled={!actionable.length || approving}
          onClick={() => setSelected(actionable.map(item => item.candidate_id))}>
          Chọn tất cả nhóm đủ bằng chứng
        </button>
        <button type="button" className="secondary" disabled={!selected.length || approving}
          onClick={() => setSelected([])}>
          Bỏ chọn
        </button>
        <button type="button" disabled={!selectedGroups.length || approving} onClick={() => void approveSelected()}>
          {approving ? "Đang kiểm tra và duyệt…" : `Duyệt nhóm đã chọn (${selectedGroups.length})`}
        </button>
      </div>
      <div className="inventory-pipeline-scroll">
        <table>
          <thead><tr><th>NGUYÊN LIỆU / BẰNG CHỨNG</th><th>DÒNG NGUỒN</th><th>ĐƠN VỊ ĐƯỢC GHI NHẬN</th><th>TRẠNG THÁI / DUYỆT NHÓM</th></tr></thead>
          <tbody>{groups.map((group,index) => <tr key={group.name+"-"+index}>
            <td>
              <strong>{group.name}</strong>
              {group.aliases.length > 1 ? <small> Tên nguồn khác: {group.aliases.join(" · ")}</small> : null}
              {group.suggested_item_name ? <small>Vật tư đề xuất: {group.suggested_item_name} (chưa chứng nhận)</small> : null}
              <details>
                <summary>Xem {group.source_rows.length} dòng bằng chứng</summary>
                <ul>{group.source_rows.map(row => <li key={row.candidate_id}>
                  <strong>{row.sheet}!{row.name_cell || `dòng ${row.row}`}</strong>
                  {" — "}{row.name} (dòng {row.row})
                  <div>Đơn vị: {row.unit_evidence.length
                    ? row.unit_evidence.map(unit => `${unit.value} [${unit.header_cell}→${unit.value_cell}]`).join(", ")
                    : "Chưa có bằng chứng đơn vị"}</div>
                </li>)}</ul>
                <p>Chứng cứ được giữ theo từng dòng nguồn; không gộp hay sửa dữ liệu gốc.</p>
              </details>
            </td>
            <td>{group.source_row_count}</td>
            <td>{group.evidence_units.join(", ") || "Chưa có bằng chứng"}</td>
            <td>
              <div>{statusLabel[group.status] || group.status}</div>
              {(group.approval_groups || []).map((approval: CatinatCandidateApprovalGroup) =>
                <div key={approval.candidate_id} className="inventory-pipeline-detail-actions">
                  <label>
                    <input type="checkbox"
                      checked={selected.includes(approval.candidate_id)}
                      disabled={!approval.can_bulk_approve || approving}
                      onChange={event => {
                        const checked = event.currentTarget.checked;
                        setSelected(current => checked
                          ? [...new Set([...current, approval.candidate_id])]
                          : current.filter(id => id !== approval.candidate_id));
                      }}
                    />
                    Duyệt nhóm {approval.sheet} · {approval.expected_count} dòng
                    {approval.item_name ? ` → ${approval.item_name}` : ""}
                  </label>
                  {approval.can_bulk_approve
                    ? <small>Bằng chứng: {approval.evidence_cells.join(", ") || "ô nguồn đã kiểm tra"}</small>
                    : <small>{blockerLabel[approval.blocker || ""] || approval.blocker || "Chưa đủ điều kiện duyệt"}</small>}
                </div>
              )}
            </td>
          </tr>)}</tbody>
        </table>
      </div>
    </details>
  </section>;
}
