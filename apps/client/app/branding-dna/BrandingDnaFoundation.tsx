import { useCallback, useEffect, useState, type FormEvent } from "react";
import "./BrandingDnaFoundation.css";

type Shop = {
 id:string;display_name:string;platform:string;region:string;storefront_url:string;
 access_mode:string;status:string;created_at:string;
};
type Status = {phase:string;shop_count:number;registered_workers:number;scanner_ready:boolean;scan_enabled:boolean;blockers:string[]};
type AccessDeclaration = {id:string;shop_id:string;relationship:string;status:string;rights_verified:false;dispatchable:false;};
type AccessReviewItem={
 shop_id:string;declaration_id:string;relationship:string;
 evidence_reference:string;declared_by:string;
 review:{id:string;decision:string;note:string;reviewed_by:string}|null;
 rights_verified:false;dispatchable:false;
};
type ShopScanReadiness={shop_id:string;display_name:string;platform:string;status:"blocked";staged_tasks:number;blockers:string[];declaration_relationship:string|null;declaration_status:string|null};
type ScanReadiness={version:1;scan_enabled:false;ready_count:0;shop_count:number;blocked_count:number;staged_tasks:number;items:ShopScanReadiness[]};
const BLOCKER_LABELS:Record<string,string>={
 scan_task_dispatch_not_deployed:"Dispatcher chưa được triển khai",
 marketplace_authorization_not_verified:"Chưa xác minh quyền theo nền tảng",
 shop_access_not_declared:"Chưa khai báo nguồn",
 shop_access_pending_verification:"Đã khai báo, đang chờ xác minh quyền",
 shop_access_needs_documentation:"Người xét duyệt yêu cầu bổ sung tài liệu",
 shop_access_documentation_rejected:"Bằng chứng bị từ chối",
 shop_access_reviewed_pending_marketplace_authorization:"Tài liệu đã ghi nhận, chưa xác minh quyền nền tảng",
 shop_competitor_research_only:"Shop đối thủ: chỉ nghiên cứu, không được cấp quyền quét",
 shop_not_active:"Shop chưa kích hoạt",
 no_staged_scan_tasks:"Chưa có task đang chờ",
 etsy_written_permission_not_verified:"Chưa xác minh quyền bằng văn bản từ Etsy",
 amazon_agent_policy_review_not_verified:"Chưa hoàn thành rà soát chính sách Amazon",
};
const RELATIONSHIP_OPTIONS=[
 {value:"unknown",text:"Chưa xác định"},
 {value:"competitor_research",text:"Shop đối thủ / nghiên cứu"},
 {value:"owned",text:"Shop tôi sở hữu"},
 {value:"authorized",text:"Shop được ủy quyền"},
] as const;

async function jsonRequest<T>(url:string, init?:RequestInit):Promise<T> {
 const response = await fetch(url, {credentials:"same-origin", cache:"no-store", ...init, signal: init?.signal ?? (!init?.method || init.method === "GET" ? AbortSignal.timeout(12000) : undefined)});
 const data = await response.json().catch(()=>null);
 if(!response.ok) throw new Error(typeof data?.detail==="string" ? data.detail : response.status===403 ? "Không đủ quyền cấu hình shop." : `API error ${response.status}`);
 return data as T;
}
export function BrandingDnaFoundation({ compact = false, onOpenShops }: { compact?: boolean; onOpenShops?: () => void } = {}) {
 const [status,setStatus]=useState<Status|null>(null);
 const [shops,setShops]=useState<Shop[]>([]);
 const [busy,setBusy]=useState(true);
 const [saving,setSaving]=useState(false);
 const [savingDeclaration,setSavingDeclaration]=useState(false);
 const [reviewingId,setReviewingId]=useState<string|null>(null);
 const [relationship,setRelationship]=useState("unknown");
 const [proofReference,setProofReference]=useState("");
 const [declarations,setDeclarations]=useState<Record<string,AccessDeclaration>>({});
 const [scanReadiness,setScanReadiness]=useState<ScanReadiness|null>(null);
 const [readinessUnavailable,setReadinessUnavailable]=useState(false);
 const [accessReviewQueue,setAccessReviewQueue]=useState<Record<string,AccessReviewItem>>({});
 const [reviewingShopId,setReviewingShopId]=useState<string|null>(null);
 const [reviewDecision,setReviewDecision]=useState("needs_documentation");
 const [reviewNote,setReviewNote]=useState("");
 const [savingReview,setSavingReview]=useState(false);
 const [notice,setNotice]=useState("");
 const [open,setOpen]=useState(false);
 const [error,setError]=useState("");
 const [platform,setPlatform]=useState("etsy");
 const [url,setUrl]=useState("");
 const refresh = useCallback(async () => {
  const [s,list,access,readiness,reviewQueue]=await Promise.all([
   jsonRequest<Status>("/api/v1/branding-dna/status"),
   jsonRequest<{items:Shop[]}>("/api/v1/branding-dna/shops"),
   jsonRequest<{items:AccessDeclaration[]}>("/api/v1/branding-dna/shops/access-declarations")
     .catch(()=>({items:[] as AccessDeclaration[]})),
   jsonRequest<ScanReadiness>("/api/v1/branding-dna/shops/scan-readiness")
     .catch(()=>null),
   jsonRequest<{items:AccessReviewItem[]}>("/api/v1/branding-dna/shops/access-reviews")
     .catch(()=>({items:[] as AccessReviewItem[]})),
  ]);
  setStatus(s);setShops(list.items);
  setDeclarations(Object.fromEntries(access.items.map(item=>[item.shop_id,item])));
  setAccessReviewQueue(Object.fromEntries(reviewQueue.items.map(item=>[item.shop_id,item])));
  setScanReadiness(readiness && readiness.version===1 && readiness.scan_enabled===false
    && readiness.ready_count===0 && readiness.items.every(item=>item.status==="blocked")
    ? readiness:null);
  setReadinessUnavailable(!readiness);
 },[]);
 useEffect(()=>{
  let active=true;
  void refresh().catch(e=>{if(active)setError(e instanceof Error?e.message:"Failed to load Shops");}).finally(()=>{if(active)setBusy(false);});
  return ()=>{active=false;};
 },[refresh]);
 async function createShop(event:FormEvent<HTMLFormElement>) {
  event.preventDefault();setSaving(true);setError("");
  try {
   await jsonRequest<Shop>("/api/v1/branding-dna/shops",{
    method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({platform,storefront_url:url.trim()})
   });
   await refresh();setOpen(false);setUrl("");
  }catch(e){setError(e instanceof Error?e.message:"Cannot save shop");}
  finally{setSaving(false);}
 }
 async function submitDeclaration(event:FormEvent<HTMLFormElement>,shopId:string) {
   event.preventDefault();setSavingDeclaration(true);setError("");setNotice("");
   try{
     await jsonRequest("/api/v1/branding-dna/shops/"+encodeURIComponent(shopId)+"/access-declarations",{
       method:"POST",headers:{"Content-Type":"application/json"},
       body:JSON.stringify({relationship,evidence_reference:proofReference.trim()})
     });
     await refresh();
     setReviewingId(null);setProofReference("");
     setNotice("Đã lưu khai báo, chưa xác minh quyền và chưa bật quét.");
   }catch(e){setError(e instanceof Error?e.message:"Không thể lưu khai báo nguồn");}
   finally{setSavingDeclaration(false);}
 }
 async function submitReview(event:FormEvent<HTMLFormElement>,shopId:string) {
   event.preventDefault();setSavingReview(true);setError("");setNotice("");
   const item=accessReviewQueue[shopId];
   if(!item){setSavingReview(false);setError("Khai báo đã thay đổi, hãy tải lại trang.");return;}
   try{
     await jsonRequest("/api/v1/branding-dna/shops/"+encodeURIComponent(shopId)+"/access-reviews",{
       method:"POST",headers:{"Content-Type":"application/json"},
       body:JSON.stringify({declaration_id:item.declaration_id,decision:reviewDecision,note:reviewNote.trim()})
     });
     await refresh();setReviewingShopId(null);setReviewNote("");
     setNotice("Đã lưu lịch sử xét duyệt. Quyền marketplace và Scan vẫn chưa được cấp.");
   }catch(e){setError(e instanceof Error?e.message:"Không thể lưu kết quả xét duyệt");}
   finally{setSavingReview(false);}
 }
 return <section className="bdna-live-section" aria-label="Branding DNA real shop registry">
  <div className="bdna-live-heading">
   <div><strong>Shop Registry</strong><small>Production data · tenant-scoped · no automatic scanning</small></div>
   {compact ? <button type="button" onClick={onOpenShops}>Mở Shop Library →</button> : <button type="button" onClick={()=>{setOpen(x=>!x);setError("");}}>{open?"Đóng biểu mẫu":"+ Thêm shop"}</button>}
  </div>
  <div className="bdna-live-summary" aria-label="Branding DNA live statistics">
   <article><small>SHOPS SAVED</small><strong>{busy?"…":status?.shop_count??0}</strong><span>Shop được lưu theo tenant</span></article>
   <article><small>WINDOWS WORKERS</small><strong>{busy?"…":status?.registered_workers??0}</strong><span>Agent đã đăng ký</span></article>
   <article className="bdna-live-not-ready"><small>SCANNER STATUS</small><strong>{status?.scanner_ready && status?.scan_enabled ? "Ready" : "Not connected"}</strong><span>{status?.scanner_ready && status?.scan_enabled ? "Đã bật trên server" : "Chờ worker và evidence pipeline"}</span></article>
  </div>
  <p className="bdna-live-info">
   <strong>Phase 1 · Foundation.</strong> Bạn có thể lưu shop vào database CAM. Tính năng Scan vẫn bị khóa trên server
   cho đến khi Browser Worker chuyên dụng, quyền truy cập và pipeline evidence được kiểm chứng.
  </p>
  {error&&<div className="bdna-live-error" role="alert">{error}</div>}
  {notice&&<p role="status" className="bdna-live-info">{notice}</p>}
  {!compact && open && <form className="bdna-shop-form" onSubmit={createShop}>
      <label>Nền tảng<select value={platform} onChange={e=>setPlatform(e.target.value)}>
    <option value="etsy">Etsy</option><option value="amazon">Amazon</option>
   </select></label>
         <label className="bdna-shop-form-url">Storefront HTTPS URL<input required inputMode="url" type="url" value={url} onChange={e=>setUrl(e.target.value)} placeholder={platform==="etsy"?"https://www.etsy.com/shop/ShopName":platform==="amazon"?"https://www.amazon.com/stores/BrandName/page/...":"https://yourshop.myshopify.com"}/></label>
   <div className="bdna-shop-form-footer"><small>Tạo shop ở trạng thái draft, không tự mở browser.</small><button type="submit" disabled={saving}>{saving?"Đang lưu…":"Lưu shop"}</button></div>
  </form>}
  {!compact && <p className="bdna-live-info" role="status">
    {scanReadiness ? `Readiness CAM: ${scanReadiness.blocked_count}/${scanReadiness.shop_count} shop bị chặn · ${scanReadiness.staged_tasks} task chờ · 0 job đang quét.`
      : readinessUnavailable ? "Readiness từng shop chưa có trên server. Không có quyền quét nào được xác nhận."
        : "Đang tải readiness từ CAM."}
  </p>}
  {!compact && (shops.length>0?<div className="bdna-shop-cards">{shops.map(shop=><article key={shop.id} className="bdna-shop-card">
   <div><strong>{shop.display_name}</strong><small>{shop.platform.toUpperCase()} · {shop.region} · {shop.access_mode}</small></div>
   <a href={shop.storefront_url} target="_blank" rel="noopener noreferrer" title="Open storefront in new tab">Nguồn gốc ↗</a>
   <span className="bdna-shop-draft">{shop.status}</span>
   <span className="bdna-shop-access-state">{declarations[shop.id]
     ? declarations[shop.id].relationship==="competitor_research"?"Nghiên cứu · không cấp quyền"
       : declarations[shop.id].status==="pending_review"?"Chờ xác minh quyền":"Đã ghi nhận khai báo"
     : "Chưa phân loại nguồn"}</span>
   {scanReadiness?.items.find(item=>item.shop_id===shop.id) && <details className="bdna-shop-blockers">
      <summary>Xem lý do chưa chạy</summary>
      <ul>{scanReadiness.items.find(item=>item.shop_id===shop.id)?.blockers.map(code=><li key={code}>{BLOCKER_LABELS[code]??code}</li>)}</ul>
      <small>{scanReadiness.items.find(item=>item.shop_id===shop.id)?.staged_tasks??0} task chờ · Chưa cấp lease hay browser job</small>
    </details>}
   <button type="button" className="bdna-declaration-toggle" onClick={()=>{
     const opening=reviewingId!==shop.id;
     setReviewingId(opening?shop.id:null);
     setRelationship("unknown");setProofReference("");setError("");
   }}>{reviewingId===shop.id?"Đóng":"Khai báo nguồn"}</button>
   {accessReviewQueue[shop.id] && <div className="bdna-review-toggle">
      <small>Hồ sơ quyền: {accessReviewQueue[shop.id].evidence_reference}</small>
      <span>{accessReviewQueue[shop.id].review
        ? `Đã xét: ${accessReviewQueue[shop.id].review?.decision}` : "Chờ người khác xét duyệt"}</span>
      <button type="button" onClick={()=>{
        const open=reviewingShopId!==shop.id;
        setReviewingShopId(open?shop.id:null);setReviewDecision("needs_documentation");
        setReviewNote("");setError("");
      }}>{reviewingShopId===shop.id?"Đóng xét duyệt":"Xét duyệt tài liệu"}</button>
    </div>}
   {reviewingShopId===shop.id && accessReviewQueue[shop.id] && <form className="bdna-access-form bdna-access-review" onSubmit={event=>void submitReview(event,shop.id)}>
     <label>Kết quả xét duyệt tài liệu
       <select value={reviewDecision} onChange={e=>setReviewDecision(e.target.value)}>
         <option value="needs_documentation">Cần thêm tài liệu</option>
         <option value="rejected">Từ chối tài liệu</option>
         <option value="documentation_noted">Đã ghi nhận tài liệu</option>
       </select>
     </label>
     <label>Ghi chú xét duyệt
       <input required minLength={8} maxLength={500} value={reviewNote}
          onChange={e=>setReviewNote(e.target.value)} placeholder="Lý do / hồ sơ cần bổ sung" />
     </label>
     <small>Chỉ người khác người khai báo được xét duyệt. Đây không phải giấy phép truy cập Amazon/Etsy, không kích hoạt tác vụ.</small>
     <button type="submit" disabled={savingReview}>{savingReview?"Đang lưu…":"Lưu xét duyệt"}</button>
   </form>}
   {reviewingId===shop.id&&<form className="bdna-access-form" onSubmit={event=>void submitDeclaration(event,shop.id)}>
     <label>Quan hệ với shop
       <select value={relationship} onChange={e=>setRelationship(e.target.value)}>
         {RELATIONSHIP_OPTIONS.map(item=><option key={item.value} value={item.value}>{item.text}</option>)}
       </select>
     </label>
     {(relationship==="owned"||relationship==="authorized")&&<label>Mã tài liệu chứng minh nội bộ
       <input required minLength={6} maxLength={240} value={proofReference}
          onChange={e=>setProofReference(e.target.value)}
          placeholder="Ví dụ: authorization-doc-2026-01"/>
     </label>}
     <small>Đây chỉ là khai báo để xét duyệt. CAM không tự xác minh, không đổi access_mode và không cấp scan job.</small>
     <button type="submit" disabled={savingDeclaration}>{savingDeclaration?"Đang lưu…":"Lưu khai báo"}</button>
   </form>}
  </article>)}</div>:<p className="bdna-empty">{busy?"Đang tải dữ liệu shop…":"Chưa có shop thật. Nhấn “Thêm shop” để bắt đầu."}</p>)}
  <details className="bdna-preview-explainer"><summary>Trạng thái dữ liệu và kết nối</summary>
   Shop Registry sử dụng dữ liệu thực theo tenant. Browser Scanner và pipeline evidence chưa kết nối;
   phần prototype độc lập chỉ dùng dữ liệu mô phỏng để duyệt thiết kế.
  </details>
 </section>;
}
