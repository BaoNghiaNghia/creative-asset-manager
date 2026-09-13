import { useEffect, useState } from "react";
import { inventoryDailySheetApi, type InventoryDailySheetStatus, type InventoryGeminiPrompt } from "./api";

const labelFor = (promptType: string) => promptType === "carry_forward_0900"
  ? "Reset đầu ngày / Carry Forward"
  : "Xử lý & đối soát Gemini cuối ngày";

const descriptionFor = (promptType: string) => promptType === "carry_forward_0900"
  ? "Hướng dẫn reset shared workbook hôm nay từ Gemini đã xác minh của ngày trước."
  : "Hướng dẫn Gemini đối soát bản sao workbook cùng ngày vào cuối ngày.";

export function InventoryGeminiPrompts() {
  const [prompts, setPrompts] = useState<InventoryGeminiPrompt[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [history, setHistory] = useState<Record<string, Array<Record<string, unknown>>>>({});
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [rerunResult, setRerunResult] = useState<Record<string, unknown> | null>(null);
  const [dailyStatus, setDailyStatus] = useState<InventoryDailySheetStatus | null>(null);

  const load = async () => {
    const [result, status] = await Promise.all([inventoryDailySheetApi.getPrompts(), inventoryDailySheetApi.getStatus()]);
    setPrompts(result.prompts);
    setDailyStatus(status);
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
    <div className="inventory-prompts-hero">
      <p className="inventory-kicker">GEMINI PROMPTS</p><h2>Hướng dẫn nghiệp vụ cho Gemini</h2><p>Soạn, lưu nháp và kích hoạt hướng dẫn cho từng luồng Inventory. Mọi giới hạn an toàn vẫn do backend kiểm soát.</p>
    </div>

    <section className="inventory-prompts-safety" role="note"><span aria-hidden="true">✓</span><div><strong>Phạm vi an toàn vẫn được backend khóa</strong><p>Quyền file, material, warehouse, Closing → Opening, blank/zero, formula, evidence và phạm vi ghi không thể thay đổi từ prompt này.</p></div></section>

    {message ? <p className={message.includes("Không thể") || message.includes("không thành công") ? "inventory-error" : "inventory-ready"} role="status">{message}</p> : null}

    <div className="inventory-prompt-grid">
      {prompts.map(prompt => {
        const current = drafts[prompt.prompt_type] || "";
        const versions = history[prompt.prompt_type] || [];
        const sourceLabel = prompt.source === "custom" ? "Tùy chỉnh đang hoạt động" : prompt.source === "legacy_config" ? "Legacy fallback" : "Prompt mặc định";
        const unsaved = current !== (prompt.active_content || prompt.builtin_content);
        const gemini = dailyStatus?.last_snapshot;
        const mode = String(dailyStatus?.agent_apply_mode || "Theo cấu hình").toUpperCase();
        return <article key={prompt.prompt_type} className="inventory-prompt-editor-card">
          <header><div><span className="inventory-prompt-step">{prompt.prompt_type === "carry_forward_0900" ? "09:00" : "DAILY"}</span><h3>{labelFor(prompt.prompt_type)}</h3><p>{descriptionFor(prompt.prompt_type)}</p></div><span className={prompt.source === "custom" ? "inventory-prompt-source custom" : "inventory-prompt-source"}>{sourceLabel}</span></header>
          <dl className="inventory-prompt-meta"><div><dt>Phiên bản</dt><dd>{prompt.version}</dd></div><div><dt>Fingerprint</dt><dd title={prompt.content_hash}>{prompt.content_hash.slice(0, 12)}…</dd></div><div><dt>Draft</dt><dd>{prompt.draft ? `v${prompt.draft.version} chờ kích hoạt` : "Không có"}</dd></div></dl>
          <label className="inventory-prompt-field"><span>Nội dung hướng dẫn</span><textarea value={current} rows={10} onChange={event => setDrafts(value => ({ ...value, [prompt.prompt_type]: event.target.value }))} /></label>
          {prompt.prompt_type === "daily_gemini_processing" ? <dl className="inventory-prompt-meta"><div><dt>File Gemini</dt><dd>{gemini?.gemini_file_id ? "Đã sẵn sàng" : "Chưa sẵn sàng"}</dd></div><div><dt>Ngày dữ liệu</dt><dd>{gemini?.business_date || "—"}</dd></div><div><dt>Mode</dt><dd>{mode}</dd></div></dl> : null}
          {prompt.prompt_type === "daily_gemini_processing" && gemini?.gemini_url ? <a className="inventory-prompt-workbook-link" href={gemini.gemini_url} target="_blank" rel="noreferrer">Mở Gemini workbook ↗</a> : null}
          <div className="inventory-prompt-actions"><button type="button" disabled={busy || !current.trim()} onClick={() => void execute(() => inventoryDailySheetApi.createPromptDraft(prompt.prompt_type, current), "Đã lưu bản nháp. Prompt này chưa được áp dụng.")}>Lưu bản nháp</button>{prompt.draft ? <button type="button" className="secondary" disabled={busy} onClick={() => window.confirm("Kích hoạt bản nháp cho lần chạy Inventory tiếp theo?") && void execute(() => inventoryDailySheetApi.activatePrompt(prompt.prompt_type, prompt.draft!.id), "Đã kích hoạt prompt cho lần chạy tiếp theo.")}>Kích hoạt draft</button> : null}{prompt.prompt_type === "daily_gemini_processing" ? <button type="button" className="secondary" disabled={busy || !gemini?.gemini_file_id} onClick={() => { if (!window.confirm(`Chạy lại Gemini trên file hiện tại?\n\nNgày dữ liệu: ${gemini?.business_date || "—"}\nPrompt Active: ${prompt.version} · ${prompt.content_hash.slice(0, 12)}…\nMode: ${mode}\n\nGemini sử dụng prompt Active hiện tại. Original snapshot không bị thay đổi. Không có file Google Sheet mới được tạo.`)) return; void execute(async () => { const result = await inventoryDailySheetApi.rerunCurrentGemini(); setRerunResult(result); }, "Đã hoàn tất chạy thử Gemini hôm nay."); }}>{busy ? "⟳ Gemini đang chạy..." : "▶ Chạy lại Gemini"}</button> : null}<button type="button" className="text-button" disabled={busy} onClick={() => void loadHistory(prompt.prompt_type)}>{versions.length ? "Làm mới lịch sử" : "Xem lịch sử"}</button><button type="button" className="text-button" disabled={busy} onClick={() => setDrafts(value => ({ ...value, [prompt.prompt_type]: prompt.active_content || prompt.builtin_content }))}>Hoàn tác chỉnh sửa</button><button type="button" className="text-danger" disabled={busy || prompt.source !== "custom"} onClick={() => window.confirm("Đặt lại prompt tùy chỉnh về fallback?") && void execute(() => inventoryDailySheetApi.resetPrompt(prompt.prompt_type), "Đặt lại fallback")}>Đặt lại fallback</button></div>
          {prompt.prompt_type === "daily_gemini_processing" && unsaved ? <p className="inventory-prompt-warning">Prompt đang chỉnh chưa được kích hoạt. Lần chạy thử sẽ dùng prompt Active hiện tại.</p> : null}
          {prompt.prompt_type === "daily_gemini_processing" && rerunResult ? <p className="inventory-ready">Status: {String(rerunResult.status)} · Writes: {String(rerunResult.writes)} · Run: {String(rerunResult.run_id || "—").slice(0, 12)}… · Prompt: {String(rerunResult.business_prompt_version || "—")}</p> : null}
          {versions.length ? <section className="inventory-prompt-history-list"><h4>Lịch sử phiên bản</h4>{versions.map(item => <div key={String(item.id)}><span><b>v{String(item.version)}</b><small>{String(item.status)} · {String(item.content_hash).slice(0, 8)}…</small></span>{item.status !== "draft" ? <button type="button" disabled={busy} onClick={() => window.confirm("Khôi phục phiên bản này thành một prompt active mới?") && void execute(() => inventoryDailySheetApi.restorePrompt(prompt.prompt_type, String(item.id)), "Đã khôi phục thành phiên bản mới.")}>Khôi phục</button> : <em>Draft hiện tại</em>}</div>)}</section> : null}
        </article>;
      })}
    </div>
  </section>;
}
