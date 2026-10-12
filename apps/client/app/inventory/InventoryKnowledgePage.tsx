import { useEffect, useMemo, useState } from "react";
import { ActionMessageToast } from "../components/ActionToast";
import {
  inventoryKnowledgeApi,
  type InventoryKnowledgeDraft,
  type InventoryKnowledgeEntry,
  type InventoryKnowledgeKind,
  type InventoryLearningStatus,
} from "./api";

const kinds: Array<{ value: InventoryKnowledgeKind; label: string }> = [
  { value: "RULE", label: "Quy tắc" },
  { value: "EXCEPTION", label: "Ngoại lệ" },
  { value: "COLUMN_MEANING", label: "Ý nghĩa cột" },
  { value: "ROW_TYPE", label: "Loại dòng" },
  { value: "FORMULA", label: "Công thức" },
  { value: "MATERIAL_MAPPING", label: "Ánh xạ vật tư" },
  { value: "WAREHOUSE_MAPPING", label: "Ánh xạ kho" },
  { value: "UNIT_CONVERSION", label: "Quy đổi đơn vị" },
  { value: "NAMING_PATTERN", label: "Quy ước tên" },
  { value: "DO_NOT_EDIT", label: "Không được sửa" },
  { value: "BUSINESS_NOTE", label: "Ghi chú nghiệp vụ" },
];

const emptyDraft = (): InventoryKnowledgeDraft => ({
  kind: "RULE",
  title: "",
  content: "",
  scope_type: "workbook",
  scope_key: "*",
  structured_rule: {},
  confidence: null,
});

function formatDate(value: string | null) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat("vi-VN", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(parsed);
}

function statusLabel(value: InventoryKnowledgeEntry["status"]) {
  return {
    active: "Đang áp dụng",
    proposed: "Gemini đề xuất",
    draft: "Bản nháp",
    archived: "Đã lưu trữ",
    rejected: "Đã từ chối",
  }[value];
}

export function isConflictingVerifiedObservation(item: Pick<InventoryKnowledgeEntry, "structured_rule">): boolean {
  const rule = item.structured_rule ?? {};
  return rule.learner === "verified_daily_assessment_v2"
    && rule.consistent_conclusion === false;
}

export function InventoryKnowledgePage() {
  const [items, setItems] = useState<InventoryKnowledgeEntry[]>([]);
  const [filter, setFilter] = useState("active,proposed,draft");
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [learning, setLearning] = useState<InventoryLearningStatus | null>(null);
  const [learningError, setLearningError] = useState(false);
  const [editing, setEditing] = useState<InventoryKnowledgeEntry | null>(null);
  const [draft, setDraft] = useState<InventoryKnowledgeDraft>(emptyDraft());
  const [ruleText, setRuleText] = useState("{}");

  const load = async () => {
    setLoading(true);
    setMessage("");
    try {
      const result = await inventoryKnowledgeApi.list(filter || undefined);
      setItems(result.items);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Không thể tải kiến thức Inventory.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, [filter]);

  useEffect(() => {
    let active = true;
    const refresh = () => {
      void inventoryKnowledgeApi.learningStatus()
        .then(result => {
          if (active) {
            setLearning(result);
            setLearningError(false);
          }
        })
        .catch(() => { if (active) setLearningError(true); });
    };
    refresh();
    // Refresh this read-only health signal while the Knowledge page is open.
    const timer = window.setInterval(refresh, 5 * 60_000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  const counts = useMemo(
    () => ({
      active: items.filter((item) => item.status === "active").length,
      proposed: items.filter((item) => item.status === "proposed").length,
      draft: items.filter((item) => item.status === "draft").length,
    }),
    [items],
  );

  const startEdit = (item: InventoryKnowledgeEntry) => {
    setEditing(item);
    setDraft({
      kind: item.kind,
      title: item.title,
      content: item.content,
      scope_type: item.scope_type,
      scope_key: item.scope_key,
      structured_rule: item.structured_rule,
      evidence: item.evidence,
      confidence: item.confidence,
    });
    setRuleText(JSON.stringify(item.structured_rule || {}, null, 2));
  };

  const resetForm = () => {
    setEditing(null);
    setDraft(emptyDraft());
    setRuleText("{}");
  };

  const save = async () => {
    let structuredRule: Record<string, unknown>;
    try {
      structuredRule = ruleText.trim() ? JSON.parse(ruleText) : {};
    } catch {
      setMessage("Structured rule phải là JSON hợp lệ.");
      return;
    }
    if (!draft.title.trim() || !draft.content.trim()) {
      setMessage("Cần nhập tiêu đề và nội dung kiến thức.");
      return;
    }
    setBusyId(editing?.id || "new");
    setMessage("");
    try {
      const payload = { ...draft, structured_rule: structuredRule };
      if (editing) {
        await inventoryKnowledgeApi.revise(editing.id, payload);
        setMessage("Đã tạo một version nháp mới. Version đang active chưa bị thay đổi.");
      } else {
        await inventoryKnowledgeApi.create(payload);
        setMessage("Đã tạo kiến thức ở trạng thái bản nháp.");
      }
      resetForm();
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Không thể lưu kiến thức.");
    } finally {
      setBusyId(null);
    }
  };

  const mutate = async (item: InventoryKnowledgeEntry, action: "activate" | "reject") => {
    setBusyId(item.id);
    setMessage("");
    try {
      if (action === "activate") {
        await inventoryKnowledgeApi.activate(item.id);
        setMessage("Đã kích hoạt version kiến thức này cho các run sau.");
      } else {
        await inventoryKnowledgeApi.reject(item.id);
        setMessage("Đã từ chối đề xuất kiến thức.");
      }
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Không thể cập nhật kiến thức.");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="inventory-knowledge">
      <header className="inventory-knowledge-hero">
        <div>
          <span>INVENTORY KNOWLEDGE</span>
          <h2>Kiến thức kiểm kê</h2>
          <p>
            Lưu các quy tắc, ngoại lệ, ý nghĩa cột và mapping đã được con người xác nhận.
            Gemini chỉ được đề xuất; chỉ version được kích hoạt mới được dùng cho các run sau.
          </p>
        </div>
        <button type="button" onClick={resetForm}>+ Kiến thức mới</button>
      </header>

      <div className="inventory-knowledge-summary">
        <article><span>Đang áp dụng</span><strong>{counts.active}</strong><small>Được inject vào Reset và Gemini</small></article>
        <article><span>Gemini đề xuất</span><strong>{counts.proposed}</strong><small>Chờ người dùng xác nhận</small></article>
        <article><span>Bản nháp</span><strong>{counts.draft}</strong><small>Chưa ảnh hưởng automation</small></article>
        <article><span>Ngày có đối soát xác minh</span><strong>{learning?.verified_days ?? "—"}</strong><small>{learning ? learning.read_cells_verified.toLocaleString("vi-VN") + " ô đã đọc và xác minh" : "Dữ liệu đang được tổng hợp"}</small></article>
        <article><span>Lần học gần nhất</span><strong>{learning?.last_learning?.business_date ?? "—"}</strong><small>{learningError ? "Không thể tải trạng thái học" : learning?.last_learning ? (learning.last_learning.status === "completed" ? "Đã kiểm tra kiến thức" : "Learning có lỗi cần kiểm tra") : "Chưa ghi nhận kết quả học"}</small></article>
        <article><span>Đề xuất mâu thuẫn</span><strong>{learning?.health?.conflicting_proposals ?? "—"}</strong><small>{learning?.health?.consistent_proposals ?? "—"} đề xuất nhất quán chờ duyệt</small></article>
      </div>
      {learning?.health && learning.health.issue_codes.length > 0 ? (
        <div className="inventory-knowledge-health-alert" role="status">
          <strong>Inventory Learning cần kiểm tra</strong>
          {learning.health.issue_codes.includes("learning_failed") ? <p>Phát hiện lỗi trong lần chạy learning gần nhất. Kiểm tra audit và log trước khi retry.</p> : null}
          {learning.health.issue_codes.includes("verified_data_stale") ? <p>Không có dữ liệu đối soát đã xác minh trong ít nhất 2 ngày. Kiểm tra Evening Reconcile và scheduler.</p> : null}
          {learning.health.issue_codes.includes("conflicting_proposals") ? <p>{learning.health.conflicting_proposals} đề xuất có kết luận không nhất quán. Không thể kích hoạt trực tiếp; cần xem bằng chứng và tạo version đã hiệu chỉnh.</p> : null}
        </div>
      ) : null}
      {learning?.health.status === "watching" ? (
        <p className="inventory-knowledge-health-note" role="status">Đang chờ lượt học mới đầu tiên sau khi nâng cấp.</p>
      ) : null}

      <ActionMessageToast message={message} />

      <div className="inventory-knowledge-layout">
        <section className="inventory-knowledge-registry">
          <div className="inventory-knowledge-toolbar">
            <div>
              <h3>Kho kiến thức</h3>
              <p>Mỗi thay đổi tạo version mới; lịch sử cũ vẫn được giữ lại.</p>
            </div>
            <select value={filter} onChange={(event) => setFilter(event.target.value)} aria-label="Lọc trạng thái kiến thức">
              <option value="active,proposed,draft">Đang dùng + chờ xử lý</option>
              <option value="active">Đang áp dụng</option>
              <option value="proposed">Gemini đề xuất</option>
              <option value="draft">Bản nháp</option>
              <option value="archived,rejected">Lịch sử</option>
              <option value="">Tất cả</option>
            </select>
          </div>

          {loading ? <div className="inventory-knowledge-empty">Đang tải kiến thức…</div> : null}
          {!loading && !items.length ? <div className="inventory-knowledge-empty">Chưa có kiến thức trong bộ lọc này.</div> : null}
          <div className="inventory-knowledge-list">
            {items.map((item) => (
              <article key={item.id} className={"inventory-knowledge-card " + item.status}>
                <div className="inventory-knowledge-card-head">
                  <div>
                    <span className={"inventory-knowledge-status " + item.status}>{statusLabel(item.status)}</span>
                    <strong>{item.title}</strong>
                    <small>{(kinds.find((kind) => kind.value === item.kind)?.label || item.kind) + " · " + item.scope_type + ":" + item.scope_key + " · v" + item.version}</small>
                  </div>
                  <time>{formatDate(item.updated_at)}</time>
                </div>
                <p>{item.content}</p>
                <div className="inventory-knowledge-meta">
                  {item.confidence !== null ? <span>{"Độ tin cậy " + Math.round(item.confidence * 100) + "%"}</span> : null}
                  <span>{"Nguồn: " + item.source}</span>
                  {isConflictingVerifiedObservation(item) ? <span className="inventory-knowledge-conflict">Kết luận mâu thuẫn · Chờ hiệu chỉnh</span> : null}
                  <span>{item.evidence.length + " evidence"}</span>
                  {item.source_run_id ? <span title={item.source_run_id}>{"Run " + item.source_run_id.slice(0, 8) + "…"}</span> : null}
                </div>
                {Object.keys(item.structured_rule || {}).length ? (
                  <details>
                    <summary>Structured rule</summary>
                    <pre>{JSON.stringify(item.structured_rule, null, 2)}</pre>
                  </details>
                ) : null}
                {item.evidence.length ? (
                  <details>
                    <summary>{"Evidence nguồn (" + item.evidence.length + ")"}</summary>
                    <pre>{JSON.stringify(item.evidence, null, 2)}</pre>
                  </details>
                ) : null}
                <div className="inventory-knowledge-actions">
                  {(item.status === "proposed" || item.status === "draft") && !isConflictingVerifiedObservation(item) ? (
                    <button disabled={busyId === item.id} onClick={() => void mutate(item, "activate")}>Kích hoạt</button>
                  ) : null}
                  <button className="secondary" disabled={busyId === item.id} onClick={() => startEdit(item)}>Tạo version mới</button>
                  {item.status === "proposed" || item.status === "draft" ? (
                    <button className="text-danger" disabled={busyId === item.id} onClick={() => void mutate(item, "reject")}>Từ chối</button>
                  ) : null}
                </div>
              </article>
            ))}
          </div>
        </section>

        <aside className="inventory-knowledge-editor">
          <div>
            <span>{editing ? "VERSION MỚI" : "KIẾN THỨC MỚI"}</span>
            <h3>{editing ? editing.title : "Tạo quy tắc / ghi chú"}</h3>
            <p>{editing ? "Version mới sẽ supersede v" + editing.version + " khi được kích hoạt." : "Lưu nháp trước; không tác động automation cho tới khi kích hoạt."}</p>
          </div>
          <label>Loại
            <select value={draft.kind} onChange={(event) => setDraft((current) => ({ ...current, kind: event.target.value as InventoryKnowledgeKind }))}>
              {kinds.map((kind) => <option key={kind.value} value={kind.value}>{kind.label}</option>)}
            </select>
          </label>
          <label>Tiêu đề
            <input value={draft.title} onChange={(event) => setDraft((current) => ({ ...current, title: event.target.value }))} placeholder="VD: Không sửa dòng TOTAL" />
          </label>
          <div className="inventory-knowledge-scope">
            <label>Scope type
              <input value={draft.scope_type} onChange={(event) => setDraft((current) => ({ ...current, scope_type: event.target.value }))} />
            </label>
            <label>Scope key
              <input value={draft.scope_key} onChange={(event) => setDraft((current) => ({ ...current, scope_key: event.target.value }))} />
            </label>
          </div>
          <label>Nội dung
            <textarea rows={7} value={draft.content} onChange={(event) => setDraft((current) => ({ ...current, content: event.target.value }))} placeholder="Mô tả nguyên tắc nghiệp vụ theo cách rõ ràng, có thể kiểm chứng." />
          </label>
          <label>Structured rule JSON
            <textarea className="code" rows={8} value={ruleText} onChange={(event) => setRuleText(event.target.value)} />
          </label>
          <div className="inventory-knowledge-editor-actions">
            <button disabled={busyId === "new" || busyId === editing?.id} onClick={() => void save()}>{editing ? "Lưu version nháp" : "Lưu bản nháp"}</button>
            <button className="secondary" onClick={resetForm}>Xóa form</button>
          </div>
        </aside>
      </div>
    </section>
  );
}
