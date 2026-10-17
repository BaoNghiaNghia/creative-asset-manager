import { useCallback, useEffect, useRef, useState, type ChangeEvent } from "react";
import "./BrandingDnaHandoffReview.css";

type Candidate={listing_key:string;title:string;image_reference_count:number;operator_note:string};
type Preview={candidate_count:number;platform:string;can_stage:boolean;blockers:string[];candidates:Candidate[]};
type Queued={id:string;listing_key:string;title:string;image_reference_count:number;operator_note:string;
  review_note:string;status:"pending_source_review"|"needs_authorized_export"|"rejected"};
type Queue={items:Queued[];count:number};
type StatusFilter="all"|Queued["status"];
const PAGE_SIZE=20;

async function request<T>(path:string,init?:RequestInit):Promise<T>{
 const response=await fetch(path,{credentials:"same-origin",cache:"no-store",...init});
 const data=await response.json().catch(()=>null);
 if(!response.ok)throw new Error(typeof data?.detail==="string"?data.detail:"API error "+response.status);
 return data as T;
}

export function BrandingDnaHandoffReview({shopId,platform,accessMode}:{
 shopId:string;platform:string;accessMode:string;
}){
 const [fileName,setFileName]=useState("");
 const [payload,setPayload]=useState<object|null>(null);
 const [preview,setPreview]=useState<Preview|null>(null);
 const [queue,setQueue]=useState<Queue|null>(null);
 const [loading,setLoading]=useState(false);
 const [busy,setBusy]=useState(false);
 const [error,setError]=useState("");
 const [notice,setNotice]=useState("");
 const [notes,setNotes]=useState<Record<string,string>>({});
 const [page,setPage]=useState(0);
 const [statusFilter,setStatusFilter]=useState<StatusFilter>("all");
 const currentShop=useRef(shopId);currentShop.current=shopId;
 const api="/api/v1/branding-dna/shops/"+encodeURIComponent(shopId)+"/handoffs";
 const queueUrl=api+"?limit="+PAGE_SIZE+"&offset="+(page*PAGE_SIZE)
   +(statusFilter==="all"?"":"&status="+encodeURIComponent(statusFilter));
 const authorized=accessMode==="owned"||accessMode==="authorized";

 const reload=useCallback(async()=>{
  if(!shopId)return;
  const expected=shopId;
  const result=await request<Queue>(queueUrl);
  if(currentShop.current!==expected)return;
  if(page>0&&result.count<=page*PAGE_SIZE){setPage(Math.max(0,Math.ceil(result.count/PAGE_SIZE)-1));return}
  setQueue(result);
 },[shopId,queueUrl,page]);
 useEffect(()=>{
  if(!shopId)return;
  const controller=new AbortController();
  setQueue(null);
  void request<Queue>(queueUrl,{signal:controller.signal})
    .then(result=>{if(!controller.signal.aborted)setQueue(result)})
    .catch(reason=>{if(!controller.signal.aborted)setError(reason instanceof Error?reason.message:"Queue unavailable")});
  return()=>controller.abort();
 },[shopId,queueUrl]);

 async function readFile(event:ChangeEvent<HTMLInputElement>){
  const file=event.target.files?.[0];event.target.value="";
  setFileName("");setPayload(null);setPreview(null);setError("");setNotice("");
  if(!file)return;
  if(file.size===0||file.size>131072){setError("File JSON phải nhỏ hơn 128 KB.");return}
  const expected=shopId;setLoading(true);
  try{
   const input=JSON.parse(await file.text()) as unknown;
   if(!input||typeof input!=="object"||Array.isArray(input))throw new Error("JSON handoff không hợp lệ.");
   const result=await request<Preview>(api+"/preview",{method:"POST",
      headers:{"Content-Type":"application/json"},body:JSON.stringify(input)});
   if(currentShop.current!==expected)return;
   setPreview(result);setPayload(input as object);setFileName(file.name);
  }catch(reason){
   if(currentShop.current===expected)setError(reason instanceof Error?reason.message:"Cannot read handoff");
  }finally{if(currentShop.current===expected)setLoading(false)}
 }
 async function stage(){
  if(!payload||!preview?.can_stage||busy)return;
  if(!window.confirm("Lưu các ứng viên chưa xác minh vào hàng chờ? Không nhập evidence và không chạy scanner."))return;
  setBusy(true);setError("");setNotice("");
  try{
   const data=await request<{created:number;replayed:number}>(api+"/stage",{
     method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
   setNotice("Đã tạo "+data.created+" ứng viên, "+data.replayed+" mục đã có. Tất cả chưa xác minh.");
   await reload();
  }catch(reason){setError(reason instanceof Error?reason.message:"Cannot stage handoff")}
  finally{setBusy(false)}
 }
 async function mark(item:Queued,status:Queued["status"]){
  if(busy)return;setBusy(true);setError("");setNotice("");
  try{
   await request(api+"/"+encodeURIComponent(item.id),{
     method:"PATCH",headers:{"Content-Type":"application/json"},
     body:JSON.stringify({status,review_note:notes[item.id]??item.review_note}),
   });
   await reload();
  }catch(reason){setError(reason instanceof Error?reason.message:"Cannot update review")}
  finally{setBusy(false)}
 }
 return <section className="bdna-handoff" aria-label="Branding DNA handoff verification">
   <header><div><h3>Handoff · Kiểm chứng nguồn</h3>
     <p>Nhận gói Pick của Scout Manager để xem trước và Stage vào hàng chờ thủ công. Không tự xác nhận nguồn, quyền ảnh hoặc training.</p>
     </div><strong>{queue?.count??0} ứng viên</strong></header>
   <label className="bdna-handoff-file">Chọn file handoff JSON (tối đa 128 KB)
     <input type="file" accept=".json,application/json" onChange={event=>{void readFile(event)}} disabled={busy||loading}/>
   </label>
   {loading&&<p role="status">Đang kiểm tra cấu trúc và shop/tenant…</p>}
   {error&&<p className="bdna-handoff-error" role="alert">{error}</p>}
   {notice&&<p className="bdna-handoff-success" role="status">{notice}</p>}
   {preview&&<div className="bdna-handoff-preview">
     <strong>Preview · {fileName} · {preview.candidate_count} ứng viên · {platform.toUpperCase()}</strong>
     <p>Preview chỉ kiểm tra định dạng và shop khớp tenant. Hash do người cung cấp chưa phải chứng thực độc lập.</p>
     {preview.candidates.map((item,index)=><div className="bdna-handoff-item" key={item.listing_key+"-"+index}>
       <b>{item.title}</b><small>{item.listing_key} · {item.image_reference_count} tham chiếu ảnh</small>
       {item.operator_note&&<small>Ghi chú: {item.operator_note}</small>}
     </div>)}
     <p className="bdna-handoff-gates">Cần kiểm chứng: {preview.blockers.map(text=>text.replaceAll("_"," ")).join(" · ")}</p>
     <button type="button" disabled={!preview.can_stage||!authorized||busy} onClick={()=>{void stage()}}>
       {busy?"Đang lưu…":"Stage vào hàng chờ xác minh"}</button>
     {!preview.can_stage&&<small>Shop cần access_mode owned/authorized trước khi Stage.</small>}
   </div>}
   <div className="bdna-handoff-queue-title"><strong>Verification Queue</strong>
      <div className="bdna-handoff-filter"><label htmlFor="handoff-status-filter">Trạng thái</label>
        <select id="handoff-status-filter" value={statusFilter} disabled={busy} onChange={event=>{setPage(0);setStatusFilter(event.target.value as StatusFilter)}}>
          <option value="all">Tất cả</option><option value="pending_source_review">Chờ kiểm chứng</option>
          <option value="needs_authorized_export">Cần nguồn chính thức</option><option value="rejected">Đã loại</option>
        </select>
        <button type="button" disabled={busy} onClick={()=>{void reload().catch(e=>setError(String(e)))}}>Làm mới</button>
      </div></div>
   {!queue?<p className="bdna-handoff-empty" role="status">Đang tải hàng chờ kiểm chứng…</p>:
    !queue.items.length?<p className="bdna-handoff-empty">Không có ứng viên phù hợp. Thử trạng thái khác hoặc chọn file để Preview.</p>:
    <div className="bdna-handoff-list">{queue.items.map(item=><article key={item.id}>
      <div><b>{item.title}</b><span>{item.status.replaceAll("_"," ")}</span></div>
      <small>{item.listing_key} · {item.image_reference_count} tham chiếu ảnh · nguồn và quyền chưa xác minh</small>
      {item.operator_note&&<p>Ghi chú Scout: {item.operator_note}</p>}
      <div className="bdna-handoff-actions">
        <input aria-label={"Ghi chú kiểm chứng "+item.title} maxLength={300}
          value={notes[item.id]??item.review_note} placeholder="Ghi chú kiểm chứng (không cấp quyền)"
          disabled={busy} onChange={e=>setNotes(old=>({...old,[item.id]:e.target.value}))}/>
        <button type="button" disabled={busy} onClick={()=>{void mark(item,"needs_authorized_export")}}>Cần nguồn chính thức</button>
        <button type="button" disabled={busy} onClick={()=>{void mark(item,"rejected")}}>Reject</button>
        <button type="button" disabled={busy} onClick={()=>{void mark(item,"pending_source_review")}}>Chờ kiểm tra</button>
      </div>
    </article>)}</div>}
   {queue&&queue.count>0&&<nav className="bdna-handoff-pages" aria-label="Phân trang ứng viên Handoff">
     <span>Hiển thị {page*PAGE_SIZE+1}–{Math.min((page+1)*PAGE_SIZE,queue.count)} / {queue.count}</span>
     <button type="button" disabled={busy||page===0} onClick={()=>setPage(current=>Math.max(0,current-1))}>Trang trước</button>
     <span>Trang {page+1} / {Math.ceil(queue.count/PAGE_SIZE)}</span>
     <button type="button" disabled={busy||(page+1)*PAGE_SIZE>=queue.count} onClick={()=>setPage(current=>current+1)}>Trang sau</button>
   </nav>}
   <footer>Gói browser handoff không thể được nhập như metadata export. Scanner vẫn tắt; quyền ảnh và lineage cần được kiểm chứng độc lập.</footer>
 </section>;
}
