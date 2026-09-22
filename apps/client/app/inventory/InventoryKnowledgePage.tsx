import { useEffect, useMemo, useState } from "react";
import {
  inventoryKnowledgeApi,
  type InventoryKnowledgeDraft,
  type InventoryKnowledgeEntry,
  type InventoryKnowledgeKind,
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

export function InventoryKnowledgePage() {
  const [items, setItems] = useState<InventoryKnowledgeEntry[]>([]);
  const [filter, setFilter] = useState("active,proposed,draft");
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [message, setMessage] = useState("");
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
      </div>

      {message ? <p className="inventory-knowledge-message" role="status">{message}</p> : null}

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
                  {item.status === "proposed" || item.status === "draft" ? (
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
