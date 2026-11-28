import {useState} from "react";
import {spyFetch} from "./api";
import "./ListingIdeaPreflightPanel.css";

type Design = {id: string; output_sha256: string; position: string; variant?: number};
type VideoLink = {url: string; aspect?: "9:16" | "16:9" | null};
type Draft = {
  design_sha256?: string; design_position?: string; garment_code?: string; shop?: string;
  template?: Record<string, unknown>; colors?: string[]; brief?: string; customer_insight?: string;
  occasion?: string; niche?: string; video_links?: VideoLink[]; rights_review_sha256?: string;
  seo_review_job_id?: string; skus?: Array<{sku: string; color: string; size: string}>;
};
type View = {
  designs: Design[]; selected_design?: Design | null; saved: Draft; seo: {job_id?: string | null; status?: string; title?: string};
  video_ugc: {count: number; status: string}; video_links: VideoLink[]; listing_image_count: number;
  blockers: string[]; warnings: string[]; ready_for_sms_payload_review: boolean;
  sms_sync_v2?: { ready_for_idea_review: boolean; readback_state: string; blockers: string[]; last_reported_readback?: { idea_code?: string } };
};
const LABELS: Record<string,string> = {
  source_metadata_missing: "Nguồn Etsy / keyword",
  select_current_design: "Chọn design hiện tại", garment_missing: "Loại áo",
  seo_draft_missing_or_incomplete: "TikTok SEO chưa đủ", seo_fact_review_required: "Duyệt thông tin SEO",
  creative_brief_missing: "Creative brief", customer_insight_missing: "Customer insight",
  occasion_missing: "Occasion", niche_missing: "Niche",
  design_rights_review_required: "Xác nhận quyền sử dụng design",
  sms_template_selection_required: "Shop / template SMS",
  sms_template_details_missing: "Giá, size chart, đóng gói, màu / size",
  template_color_selection_required: "Màu phù hợp template",
  google_drive_video_links_missing: "Link Google Drive video",
};
const designKey = (d: Design) => d.output_sha256 + ":" + d.position;
const linkText = (links?: VideoLink[]) => (links || []).map(v => (v.aspect ? v.aspect + " | " : "") + v.url).join("\n");
const parseLinks = (text: string): VideoLink[] => text.split(/\r?\n/).map(v=>v.trim()).filter(Boolean).map(line => {
  const m = line.match(/^(9:16|16:9)\s*\|\s*(.+)$/);
  return m ? {aspect: m[1] as VideoLink["aspect"], url: m[2].trim()} : {url: line};
});

export function ListingIdeaPreflightPanel({listingKey, canManage}: {listingKey: string; canManage: boolean}) {
  const [open,setOpen] = useState(false);
  const [view,setView] = useState<View|null>(null);
  const [draft,setDraft] = useState<Draft>({});
  const [templateText,setTemplateText] = useState("");
  const [videos,setVideos] = useState("");
  const [colors,setColors] = useState("");
  const [busy,setBusy] = useState(false);
  const [error,setError] = useState("");
  const [message,setMessage] = useState("");
  const path = "/listings/" + encodeURIComponent(listingKey) + "/listing-idea-preflight";
  const apply = (next: View) => {
    const value = next.saved || {};
    const selected = next.selected_design;
    const initial = {...value, design_sha256: value.design_sha256 || selected?.output_sha256,
      design_position: value.design_position || selected?.position};
    setView(next); setDraft(initial);
    setTemplateText(value.template ? JSON.stringify(value.template,null,2) : "");
    setVideos(linkText(value.video_links));
    setColors((value.colors || []).join(", "));
  };
  const load = async () => {
    setBusy(true); setError(""); setMessage("");
    try {apply(await spyFetch<View>(path));}
    catch(err){setError(err instanceof Error ? err.message : "Không thể tải dữ liệu");}
    finally {setBusy(false);}
  };
  const toggle = () => {
    const next = !open; setOpen(next);
    if(next && !view && !busy) void load();
  };
  const update = (key: keyof Draft, value: string) => setDraft(prev => ({...prev,[key]:value}));
  const selectDesign = (key: string) => {
    const selected = view?.designs.find(d=>designKey(d)===key);
    setDraft(prev=>({...prev,design_sha256:selected?.output_sha256,
      design_position:selected?.position,rights_review_sha256:undefined}));
  };
  const importDraft = async (file: File | undefined) => {
    if(!file || file.size > 128*1024) {setError("File JSON tối đa 128 KB.");return;}
    try {
      const value = JSON.parse(await file.text()) as Draft & {listingKey?: string};
      if(!value || typeof value!=="object" || Array.isArray(value) ||
        (value.listingKey && value.listingKey!==listingKey) ||
        !value.template || typeof value.template!=="object" || Array.isArray(value.template)) throw new Error("File template không hợp lệ hoặc không đúng sản phẩm.");
      const selected = view?.designs.find(d=>d.output_sha256===value.design_sha256 && d.position===value.design_position);
      setDraft(prev=>({...prev,
        shop:typeof value.shop==="string"?value.shop:prev.shop,
        garment_code:typeof value.garment_code==="string"?value.garment_code:prev.garment_code,
        design_sha256:selected?.output_sha256 || prev.design_sha256,
        design_position:selected?.position || prev.design_position,
        rights_review_sha256:selected && selected.output_sha256!==prev.design_sha256?undefined:prev.rights_review_sha256,
        template:value.template}));
      setTemplateText(JSON.stringify(value.template,null,2));
      if(Array.isArray(value.colors))setColors(value.colors.filter(c=>typeof c==="string").join(", "));
      setError("");setMessage("Đã nhập template vào bản nháp. Bấm Lưu để ghi vào CAM.");
    } catch(err) {setError(err instanceof Error?err.message:"File JSON không hợp lệ.");}
  };
  const save = async () => {
    if(!canManage || busy) return;
    setBusy(true);setError("");setMessage("");
    try {
      const template = templateText.trim() ? JSON.parse(templateText) : undefined;
      const body: Draft = {...draft,template,colors:colors.split(",").map(c=>c.trim()).filter(Boolean),
        video_links:parseLinks(videos)};
      delete (body as Draft & {updated_at?: string}).updated_at;
      const next=await spyFetch<View>(path,{method:"PUT",body:JSON.stringify(body)});
      apply(next);setMessage("Đã lưu bản nháp vào CAM. Chưa gửi SMS.");
    } catch(err){setError(err instanceof Error?err.message:"Không thể lưu dữ liệu");}
    finally{setBusy(false);}
  };
  const currentDesign = draft.design_sha256 && draft.design_position ? draft.design_sha256 + ":" + draft.design_position : "";
  return <section className="etsy-idea-preflight">
    <div className="etsy-idea-preflight-heading">
      <div><strong>Chuẩn bị Listing Idea</strong><small>Dữ liệu CAM + template SMS · ảnh bổ sung sau</small></div>
      <button type="button" onClick={toggle} aria-expanded={open}>{open?"Thu gọn":"Kiểm tra data"}</button>
    </div>
    {open && <div className="etsy-idea-preflight-body">
      {busy && <p role="status">Đang xử lý…</p>}
      {error && <p role="alert">{error}</p>}
      {message && <p role="status">{message}</p>}
      {view && <>
        {view.sms_sync_v2 && <div className="etsy-idea-preflight-summary" aria-label="SMS Idea sync status">
          <span>{view.sms_sync_v2.ready_for_idea_review ? "Idea preflight: đủ dữ liệu" : "Idea preflight: còn " + view.sms_sync_v2.blockers.length + " mục"}</span>
          <span>{view.sms_sync_v2.readback_state === "reported_counts_match" ? "SMS: đã ghi nhận đối soát" : view.sms_sync_v2.readback_state === "stale_report" ? "SMS: cần đối soát lại" : "SMS: chưa có receipt"}{view.sms_sync_v2.last_reported_readback?.idea_code ? " · " + view.sms_sync_v2.last_reported_readback.idea_code : ""}</span>
        </div>}
        <div className="etsy-idea-preflight-summary">
          <span>{view.ready_for_sms_payload_review?"Đủ dữ liệu để duyệt":"Còn " + view.blockers.length + " mục cần bổ sung"}</span>
          <span>SEO: {view.seo.title?"Có bản nháp":"Chưa có"}</span>
          <span>Video CAM: {view.video_ugc.count}</span>
          <span>Ảnh: {view.listing_image_count} (không bắt buộc)</span>
        </div>
        {view.blockers.length>0 && <ul className="etsy-idea-preflight-missing">{view.blockers.map(b=><li key={b}>{LABELS[b] || b}</li>)}</ul>}
        <p className="etsy-idea-preflight-note">Chỉ lưu dữ liệu nội bộ. Idea V2 không yêu cầu template, giá hay đủ bộ ảnh; các mục duyệt bên dưới dành cho bước chuẩn bị đăng bán. Kiểm tra trùng Idea và link Drive trước khi chuyển SMS. Receipt chỉ xác nhận số lượng và link đã đọc, không xác minh phát video.</p>
        <fieldset disabled={!canManage || busy}>
          <label>Design<select value={currentDesign} onChange={e=>selectDesign(e.target.value)}>
            <option value="">Chọn thiết kế</option>
            {view.designs.map(d=><option key={designKey(d)} value={designKey(d)}>{d.position} · {d.output_sha256.slice(0,12)}</option>)}
          </select></label>
          <div className="etsy-idea-preflight-grid">
            <label>Garment<select value={draft.garment_code||""} onChange={e=>update("garment_code",e.target.value)}><option value="">Chọn áo</option>
              {["C1717","G500","G180","G185"].map(g=><option key={g}>{g}</option>)}</select></label>
            <label>Shop SMS<input value={draft.shop||""} onChange={e=>update("shop",e.target.value)} maxLength={120}/></label>
            <label>Occasion<input value={draft.occasion||""} onChange={e=>update("occasion",e.target.value)} maxLength={180}/></label>
            <label>Niche<input value={draft.niche||""} onChange={e=>update("niche",e.target.value)} maxLength={180}/></label>
          </div>
          <label>Màu áo (theo template, cách nhau bằng dấu phẩy)<input value={colors} onChange={e=>setColors(e.target.value)}/></label>
          <label>Creative brief<textarea rows={2} value={draft.brief||""} onChange={e=>update("brief",e.target.value)} maxLength={4000}/></label>
          <label>Customer insight<textarea rows={2} value={draft.customer_insight||""} onChange={e=>update("customer_insight",e.target.value)} maxLength={4000}/></label>
          <label>Video Google Drive<textarea rows={3} value={videos} onChange={e=>setVideos(e.target.value)}
            placeholder="Mỗi dòng một link, hoặc 9:16 | URL và 16:9 | URL; chấp nhận link thư mục."/></label>
          <details><summary>Template SMS · nhập dữ liệu</summary>
            <label>File JSON<input type="file" accept=".json,application/json" onChange={e=>void importDraft(e.target.files?.[0])}/></label>
            <label>Template JSON<textarea rows={5} value={templateText} onChange={e=>setTemplateText(e.target.value)}/></label>
          </details>
          <label className="etsy-idea-preflight-check"><input type="checkbox" disabled={!draft.design_sha256}
            checked={!!draft.design_sha256&&draft.rights_review_sha256===draft.design_sha256}
            onChange={e=>setDraft(prev=>({...prev,rights_review_sha256:e.target.checked?prev.design_sha256:undefined}))}/> Đã xác nhận quyền sử dụng design này</label>
          <label className="etsy-idea-preflight-check"><input type="checkbox" disabled={!view.seo.job_id||view.seo.status!=="needs_fact_check"}
            checked={!!view.seo.job_id&&draft.seo_review_job_id===view.seo.job_id}
            onChange={e=>setDraft(prev=>({...prev,seo_review_job_id:e.target.checked?(view.seo.job_id||undefined):undefined}))}/> Đã duyệt các thông tin trong SEO hiện tại</label>
        </fieldset>
        <div className="etsy-idea-preflight-actions">
          <button type="button" disabled={busy} onClick={()=>void load()}>Tải lại</button>
          {canManage&&<button type="button" disabled={busy} onClick={()=>void save()}>Lưu dữ liệu CAM</button>}
        </div>
      </>}
      {!view&&!busy&&<button type="button" onClick={()=>void load()}>Thử lại</button>}
    </div>}
  </section>;
}
