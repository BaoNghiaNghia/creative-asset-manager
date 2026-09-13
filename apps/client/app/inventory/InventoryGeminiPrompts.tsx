import { useEffect, useState } from "react";
import { inventoryDailySheetApi, type InventoryGeminiPrompt } from "./api";

const labelFor = (promptType: string) => promptType === "carry_forward_0900"
  ? "Carry Forward 09:00"
  : "Xử lý file Gemini hằng ngày";

const descriptionFor = (promptType: string) => promptType === "carry_forward_0900"
  ? "Hướng dẫn Gemini đối chiếu Closing đã xác minh và ghi Opening cho ngày mới."
  : "Hướng dẫn Gemini đọc và chuẩn hóa bản sao workbook làm việc hằng ngày.";

export function InventoryGeminiPrompts() {
  const [prompts, setPrompts] = useState<InventoryGeminiPrompt[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [history, setHistory] = useState<Record<string, Array<Record<string, unknown>>>>({});
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const load = async () => {
    const result = await inventoryDailySheetApi.getPrompts();
    setPrompts(result.prompts);
    setDrafts(Object.fromEntries(result.prompts.map(prompt => [prompt.prompt_type, prompt.draft?.content || prompt.active_content || prompt.builtin_content])));
  };

  useEffect(() => { void load().catch(error => setMessage(error instanceof Error ? error.message : "Không thể tải Gemini Prompts.")); }, []);

  const execute = async (operation: () => Promise<unknown>, success: string) => {
    setBusy(true); setMessage("");
    try { await operation(); await load(); setMessage(success); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Thao tác không thành công."); }
    finally { setBusy(false); }
  };

  const loadHistory = async (promptType: InventoryGeminiPrompt["prompt_type"]) => {
    try {
      const result = await inventoryDailySheetApi.getPromptVersions(promptType);
      setHistory(current => ({ ...current, [promptType]: result.versions }));
    } catch (error) { setMessage(error instanceof Error ? error.message : "Không thể tải lịch sử phiên bản."); }
  };

  return <section className="inventory-prompts-page">
    <header className="inventory-prompts-hero">
      <div><p className="inventory-kicker">GEMINI PROMPTS</p><h2>Hướng dẫn nghiệp vụ cho Gemini</h2><p>Chỉnh nội dung hướng dẫn theo quy trình Inventory, với versioning, lịch sử và khả năng khôi phục rõ ràng.</p></div>
      <aside><strong>{prompts.length}</strong><span>luồng nghiệp vụ</span></aside>
    </header>

    <section className="inventory-prompts-safety" role="note"><span aria-hidden="true">✓</span><div><strong>Phạm vi an toàn vẫn được backend khóa</strong><p>Quyền file, material, warehouse, Closing → Opening, blank/zero, formula, evidence và phạm vi ghi không thể thay đổi từ prompt này.</p></div></section>

    {message ? <p className={message.includes("Không thể") || message.includes("không thành công") ? "inventory-error" : "inventory-ready"} role="status">{message}</p> : null}

    <div className="inventory-prompt-grid">
      {prompts.map(prompt => {
        const current = drafts[prompt.prompt_type] || "";
        const versions = history[prompt.prompt_type] || [];
        const sourceLabel = prompt.source === "custom" ? "Tùy chỉnh đang hoạt động" : prompt.source === "legacy_config" ? "Legacy fallback" : "Prompt mặc định";
        return <article key={prompt.prompt_type} className="inventory-prompt-editor-card">
          <header><div><span className="inventory-prompt-step">{prompt.prompt_type === "carry_forward_0900" ? "09:00" : "DAILY"}</span><h3>{labelFor(prompt.prompt_type)}</h3><p>{descriptionFor(prompt.prompt_type)}</p></div><span className={prompt.source === "custom" ? "inventory-prompt-source custom" : "inventory-prompt-source"}>{sourceLabel}</span></header>
          <dl className="inventory-prompt-meta"><div><dt>Phiên bản</dt><dd>{prompt.version}</dd></div><div><dt>Fingerprint</dt><dd title={prompt.content_hash}>{prompt.content_hash.slice(0, 12)}…</dd></div><div><dt>Draft</dt><dd>{prompt.draft ? `v${prompt.draft.version} chờ kích hoạt` : "Không có"}</dd></div></dl>
          <label className="inventory-prompt-field"><span>Nội dung hướng dẫn</span><textarea value={current} rows={10} onChange={event => setDrafts(value => ({ ...value, [prompt.prompt_type]: event.target.value }))} /></label>
          <div className="inventory-prompt-actions"><button type="button" disabled={busy || !current.trim()} onClick={() => void execute(() => inventoryDailySheetApi.createPromptDraft(prompt.prompt_type, current), "Đã lưu bản nháp. Prompt này chưa được áp dụng.")}>Lưu bản nháp</button>{prompt.draft ? <button type="button" className="secondary" disabled={busy} onClick={() => window.confirm("Kích hoạt bản nháp cho lần chạy Inventory tiếp theo?") && void execute(() => inventoryDailySheetApi.activatePrompt(prompt.prompt_type, prompt.draft!.id), "Đã kích hoạt prompt cho lần chạy tiếp theo.")}>Kích hoạt draft</button> : null}<button type="button" className="text-button" disabled={busy} onClick={() => void loadHistory(prompt.prompt_type)}>{versions.length ? "Làm mới lịch sử" : "Xem lịch sử"}</button><button type="button" className="text-button" disabled={busy} onClick={() => setDrafts(value => ({ ...value, [prompt.prompt_type]: prompt.active_content || prompt.builtin_content }))}>Hoàn tác chỉnh sửa</button><button type="button" className="text-danger" disabled={busy || prompt.source !== "custom"} onClick={() => window.confirm("Đặt lại prompt tùy chỉnh về fallback?") && void execute(() => inventoryDailySheetApi.resetPrompt(prompt.prompt_type), "Đã đặt lại về prompt fallback.")}>Đặt lại fallback</button></div>
          {versions.length ? <section className="inventory-prompt-history-list"><h4>Lịch sử phiên bản</h4>{versions.map(item => <div key={String(item.id)}><span><b>v{String(item.version)}</b><small>{String(item.status)} · {String(item.content_hash).slice(0, 8)}…</small></span>{item.status !== "draft" ? <button type="button" disabled={busy} onClick={() => window.confirm("Khôi phục phiên bản này thành một prompt active mới?") && void execute(() => inventoryDailySheetApi.restorePrompt(prompt.prompt_type, String(item.id)), "Đã khôi phục thành phiên bản mới.")}>Khôi phục</button> : <em>Draft hiện tại</em>}</div>)}</section> : null}
        </article>;
      })}
    </div>
  </section>;
}
