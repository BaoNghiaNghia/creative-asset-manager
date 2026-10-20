import {useEffect,useState,type FormEvent} from "react";
import "./BrandingDnaBrowserReviewQueue.css";

type ReviewState="pending_independent_review"|"needs_authorized_source"|"metadata_consistent"|"rejected";
type QueueItem={id:string;shop_id:string;platform:"amazon"|"etsy";report_key:string;
 observed_count:number;created_at:string;review_state:ReviewState;
 source_verified:false;rights_verified:false;scan_enabled:false};
type QueueResponse={items:QueueItem[];count:number;limit:number;offset:number;
 source_verified:false;rights_verified:false;scan_enabled:false;dispatchable:false};
type Listing={listing_key:string;title:string;image_count:number;rights_state:"unknown";
 source_verification:"unverified";sales_state:"unknown"};
type Detail={id:string;items:Listing[];report_key:string;shop_id:string;
 source_verification:"unverified";rights_verified:false;scan_enabled:false};
type Audit={id:string;decision:ReviewState;rights_basis:string;evidence_reference:string|null;
 note:string;reviewed_by:string;created_at:string;source_verified:false;scan_enabled:false};
type History={items:Audit[];latest_decision:ReviewState;review_count_returned:number;
 source_verified:false;rights_verified:false;scan_enabled:false};

const STATES:Record<ReviewState,string>={
 pending_independent_review:"Chưa được kiểm duyệt",
 needs_authorized_source:"Cần đối chiếu nguồn chính thức",
 metadata_consistent:"Metadata hợp lý · chưa xác minh",
 rejected:"Từ chối hồ sơ",
};
const BASE="/api/v1/branding-dna";
async function request<T>(path:string,signal?:AbortSignal,init?:RequestInit):Promise<T>{
 const response=await fetch(path,{credentials:"same-origin",cache:"no-store",...init,
   signal:signal??init?.signal??AbortSignal.timeout(15000)});
 const body=await response.json().catch(()=>null);
 if(!response.ok)throw new Error(typeof body?.detail==="string"?body.detail:
  response.status===403?"Bạn chưa có quyền xem/lưu xét duyệt.":"Không thể truy cập CAM ("+response.status+").");
 return body as T;
}
function assertUnverified(result:{source_verified?:boolean;rights_verified?:boolean;scan_enabled?:boolean}){
 if(result.source_verified!==false||result.rights_verified!==false||result.scan_enabled!==false)
  throw new Error("Phản hồi CAM không đáp ứng hợp đồng an toàn.");
}

export function BrandingDnaBrowserReviewQueue(){
 const [filter,setFilter]=useState<ReviewState|"all">("pending_independent_review");
 const [page,setPage]=useState(0);
 const [reload,setReload]=useState(0);
 const [data,setData]=useState<QueueResponse|null>(null);
 const [loading,setLoading]=useState(true);
 const [selected,setSelected]=useState<QueueItem|null>(null);
 const [detail,setDetail]=useState<Detail|null>(null);
 const [history,setHistory]=useState<History|null>(null);
 const [detailLoading,setDetailLoading]=useState(false);
 const [decision,setDecision]=useState<Exclude<ReviewState,"pending_independent_review">>("needs_authorized_source");
 const [rights,setRights]=useState<"unknown"|"consent_reference_recorded"|"permission_missing">("unknown");
 const [reference,setReference]=useState("");
 const [note,setNote]=useState("");
 const [saving,setSaving]=useState(false);
 const [notice,setNotice]=useState("");
 const [error,setError]=useState("");
 const [canReview,setCanReview]=useState(true);
 const limit=20;

 useEffect(()=>{
  const controller=new AbortController();
  setLoading(true);setError("");setData(null);setSelected(null);setDetail(null);setHistory(null);
  const q=new URLSearchParams({limit:String(limit),offset:String(page*limit)});
  if(filter!=="all")q.set("state",filter);
  void request<QueueResponse>(BASE+"/browser-observations/review-queue?"+q,controller.signal)
    .then(result=>{
      assertUnverified(result);
      if(!controller.signal.aborted)setData(result);
    }).catch(e=>{if(!controller.signal.aborted)setError(String(e));})
    .finally(()=>{if(!controller.signal.aborted)setLoading(false);});
  return ()=>controller.abort();
 },[filter,page,reload]);

 useEffect(()=>{
  if(!selected)return;
  const controller=new AbortController();
  const endpoint=BASE+"/shops/"+encodeURIComponent(selected.shop_id)+
    "/browser-observations/"+encodeURIComponent(selected.id);
  setDetail(null);setHistory(null);setCanReview(true);setDetailLoading(true);
  setError("");setNotice("");
  void Promise.all([
    request<Detail>(endpoint,controller.signal),
    request<History>(endpoint+"/reviews",controller.signal),
  ]).then(([d,h])=>{
    if(d.source_verification!=="unverified"||d.rights_verified!==false||
       d.scan_enabled!==false)throw new Error("Chi tiết báo cáo không đáp ứng hợp đồng an toàn.");
    assertUnverified(h);
    if(!controller.signal.aborted){setDetail(d);setHistory(h);}
  }).catch(e=>{if(!controller.signal.aborted)setError(String(e));})
    .finally(()=>{if(!controller.signal.aborted)setDetailLoading(false);});
  return ()=>controller.abort();
 },[selected?.id,reload]);

 async function submit(event:FormEvent<HTMLFormElement>){
  event.preventDefault();
  if(!selected||saving)return;
  setSaving(true);setError("");setNotice("");
  try{
    const endpoint=BASE+"/shops/"+encodeURIComponent(selected.shop_id)+
      "/browser-observations/"+encodeURIComponent(selected.id)+"/reviews";
    const result=await request<Audit>(endpoint,undefined,{
      method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({decision,rights_basis:rights,
        evidence_reference:rights==="consent_reference_recorded"?reference.trim():null,
        note:note.trim()}),
    });
    assertUnverified(result);
    setNotice("Đã lưu biên bản. Metadata, quyền sử dụng ảnh và nguồn vẫn chưa được xác minh.");
    setNote("");setReference("");
    const h=await request<History>(endpoint);
    assertUnverified(h);
    setHistory(h);
    setData(previous=>previous?{...previous,
      items:previous.items.map(item=>item.id===selected.id
        ?{...item,review_state:decision}:item),
    }:previous);
    setSelected(previous=>previous?.id===selected.id
      ?{...previous,review_state:decision}:previous);
  }catch(e){
    const message=e instanceof Error?e.message:"Không thể lưu biên bản.";
    if(message.includes("importer cannot review")||message.includes("cannot review their own"))
      setCanReview(false);
    setError(message);
  }finally{setSaving(false);}
 }
 return <section className="bdna-browser-reviews" aria-label="Browser Pilot independent Review Queue">
  <header>
   <div><h3>Browser Pilot · Review Queue</h3>
    <p>Kiểm duyệt báo cáo trình duyệt theo shop. Không mở marketplace, không cấp scan job và không xác minh tự động.</p>
   </div>
   <div className="bdna-browser-reviews-controls">
    <label>Trạng thái
     <select aria-label="Lọc báo cáo" value={filter} onChange={e=>{
       setFilter(e.target.value as ReviewState|"all");setPage(0);}}>
      <option value="all">Tất cả báo cáo</option>
      {Object.entries(STATES).map(([value,label])=><option key={value} value={value}>{label}</option>)}
     </select>
    </label>
    <button type="button" onClick={()=>setReload(x=>x+1)}>Làm mới</button>
   </div>
  </header>
  <p className="bdna-browser-reviews-warning">Báo cáo DOM chỉ chứa metadata và mã băm tham chiếu; không có ảnh được cấp quyền, số bán xác thực hay bằng chứng đủ để tự chạy agent.</p>
  {error&&<p role="alert" className="bdna-browser-reviews-error">{error}</p>}
  {notice&&<p role="status" className="bdna-browser-reviews-message">{notice}</p>}
  {loading?<p role="status">Đang tải Review Queue…</p>:
    !data?.items.length?<p className="bdna-browser-reviews-empty">Chưa có báo cáo ở trạng thái này. Không tạo dữ liệu thử trên production.</p>:
    <div className="bdna-browser-reviews-list">
     {data.items.map(item=><button type="button" key={item.id}
       className={selected?.id===item.id?"selected":""} aria-pressed={selected?.id===item.id}
       onClick={()=>{setSelected(item);setDecision("needs_authorized_source");setRights("unknown");setNote("");setReference("");}}>
       <strong>{item.platform.toUpperCase()} · {item.observed_count} sản phẩm</strong>
       <span>{STATES[item.review_state]??item.review_state}</span>
       <small>Shop {item.shop_id.slice(0,8)}… · {new Date(item.created_at).toLocaleString("vi-VN")}</small>
     </button>)}
    </div>}
  {!!data&&<div className="bdna-browser-reviews-pages">
   <span>{data.count} báo cáo · Trang {page+1}</span>
   <button type="button" disabled={page===0||loading} onClick={()=>setPage(x=>x-1)}>← Trước</button>
   <button type="button" disabled={(page+1)*limit>=data.count||loading} onClick={()=>setPage(x=>x+1)}>Tiếp →</button>
  </div>}
  {selected&&<section className="bdna-browser-review-detail" aria-label="Chi tiết và lịch sử xét duyệt">
   <h4>Báo cáo {selected.report_key.slice(0,20)}…</h4>
   {detailLoading?<p role="status">Đang tải chi tiết…</p>:
   detail&&history&&<><p>{detail.items.length} quan sát · {STATES[history.latest_decision]??history.latest_decision}. Nội dung chưa được xác minh nguồn.</p>
    <ol className="bdna-browser-review-listings">
     {detail.items.map(item=><li key={item.listing_key}>
      <strong>{item.title}</strong><small>{item.listing_key} · {item.image_count} tham chiếu ảnh · Quyền ảnh: unknown · Doanh số: unknown</small>
     </li>)}
    </ol>
    {canReview&&<form onSubmit={e=>void submit(e)}>
     <label>Đánh giá metadata
      <select aria-label="Quyết định" value={decision} onChange={e=>setDecision(e.target.value as typeof decision)}>
       {Object.entries(STATES).filter(([state])=>state!=="pending_independent_review")
        .map(([state,label])=><option key={state} value={state}>{label}</option>)}
      </select>
     </label>
     <label>Căn cứ quyền ảnh
      <select aria-label="Căn cứ quyền ảnh" value={rights} onChange={e=>setRights(e.target.value as typeof rights)}>
       <option value="unknown">Chưa rõ</option>
       <option value="consent_reference_recorded">Đã ghi nhận mã hồ sơ đồng ý</option>
       <option value="permission_missing">Thiếu giấy phép</option>
      </select>
     </label>
     {rights==="consent_reference_recorded"&&<label>Mã hồ sơ
      <input value={reference} minLength={8} maxLength={160} required
       onChange={e=>setReference(e.target.value)} placeholder="consent-reference-2026"/>
     </label>}
     <label>Ghi chú kiểm duyệt
      <textarea value={note} minLength={8} maxLength={500} required rows={2}
       onChange={e=>setNote(e.target.value)}
       placeholder="Nêu nguồn cần xác thực hoặc lý do từ chối"/>
     </label>
     <button type="submit" disabled={saving}>{saving?"Đang lưu…":"Ghi nhận đánh giá · Không xác minh tự động"}</button>
     <small>Người nhập báo cáo không được tự duyệt. Quyết định chỉ thêm lịch sử, không mở browser hoặc cấp quyền huấn luyện.</small>
    </form>}
    <details open>
     <summary>Lịch sử kiểm duyệt ({history.items.length})</summary>
     {history.items.length===0?<p>Chưa có người kiểm duyệt.</p>:
      <ol>{history.items.map(item=><li key={item.id}><strong>{STATES[item.decision]??item.decision}</strong>
       <small>{item.reviewed_by} · {new Date(item.created_at).toLocaleString("vi-VN")}</small>
       <span>{item.note}</span>
      </li>)}</ol>}
    </details>
   </>}
  </section>}
 </section>;
}
