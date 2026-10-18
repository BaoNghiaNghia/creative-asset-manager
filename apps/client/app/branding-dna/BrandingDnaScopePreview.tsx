import {useEffect,useState,type FormEvent} from "react";
import "./BrandingDnaScopePreview.css";

type Task={id:string;status:string;task_kind:string};
type Preview={
  shop_id:string;task_id:string;candidate_within_storefront:boolean;
  scope_reason:string;shop_access_declared:boolean;
  limits:{max_pages:number;max_gallery_items:number;max_navigation_seconds:number;
          max_redirects:number;max_extra_path_components:number;request_allowed:false};
  job:null;dispatchable:false;scan_enabled:false;marketplace_authorization_verified:false;
  blockers:string[];
};
const labels:Record<string,string>={
  registered_storefront_subpath:"URL thuộc nhánh storefront đã đăng ký.",
  registered_storefront_not_supported:"URL shop đã đăng ký không nằm trong bộ kiểm tra phạm vi được hỗ trợ.",
  platform_not_supported:"Nền tảng chưa nằm trong phạm vi thử nghiệm.",
  candidate_url_invalid:"URL không hợp lệ hoặc có thành phần bị cấm.",
  candidate_host_outside_shop:"Hostname khác với storefront đã đăng ký.",
  candidate_path_outside_shop:"Đường dẫn nằm ngoài nhánh storefront.",
  candidate_path_depth_exceeded:"Đường dẫn vượt quá độ sâu được phép.",
};
const base=(shopId:string)=>"/api/v1/branding-dna/shops/"+encodeURIComponent(shopId);

export function BrandingDnaScopePreview({shopId}:{shopId:string}){
 const [tasks,setTasks]=useState<Task[]>([]);
 const [taskId,setTaskId]=useState("");
 const [url,setUrl]=useState("");
 const [loading,setLoading]=useState(false);
 const [error,setError]=useState("");
 const [result,setResult]=useState<Preview|null>(null);
 useEffect(()=>{
  const controller=new AbortController();
  setResult(null);setTasks([]);setTaskId("");setUrl("");setError("");
  if(!shopId)return ()=>controller.abort();
  void fetch(base(shopId)+"/scan-tasks?limit=100",{
    credentials:"same-origin",cache:"no-store",signal:controller.signal,
  }).then(async res=>{
    if(!res.ok)throw Error("Không thể tải staged tasks ("+res.status+").");
    return await res.json() as {items:Task[]};
  }).then(data=>{
    if(controller.signal.aborted)return;
    const staged=data.items.filter(t=>t.status==="staged");
    setTasks(staged);setTaskId(staged[0]?.id??"");
  }).catch(e=>{if(!controller.signal.aborted)setError(e instanceof Error?e.message:String(e))});
  return ()=>controller.abort();
 },[shopId]);
 async function preview(event:FormEvent){
  event.preventDefault();
  if(!shopId||!taskId||!url||loading)return;
  setLoading(true);setResult(null);setError("");
  try{
   const res=await fetch(base(shopId)+"/scan-scope/preview",{
     method:"POST",credentials:"same-origin",cache:"no-store",
     headers:{"Content-Type":"application/json"},
     body:JSON.stringify({task_id:taskId,candidate_url:url}),
   });
   const data=await res.json() as unknown;
   if(!res.ok){
     const detail=(data&&typeof data==="object"&&"detail" in data)?data.detail:undefined;
     throw Error(typeof detail==="string"?detail:"Không thể kiểm tra phạm vi ("+res.status+").");
   }
   const answer=data as Preview;
   if(answer.shop_id!==shopId||answer.task_id!==taskId||answer.job!==null||
      answer.dispatchable!==false||answer.scan_enabled!==false||
      answer.marketplace_authorization_verified!==false||
      answer.limits?.request_allowed!==false)
     throw Error("Phản hồi không đáp ứng chế độ kiểm tra an toàn.");
   setResult(answer);
  }catch(e){setError(e instanceof Error?e.message:String(e))}
  finally{setLoading(false)}
 }
 return <section className="bdna-scope-preview" aria-label="Kiểm tra phạm vi URL shop">
   <header><h4>Kiểm tra phạm vi URL · chỉ xem trước</h4>
    <p>Chọn staged task và nhập URL để kiểm tra đúng storefront. Không truy cập URL, không cấp quyền, không phát job.</p></header>
   <form onSubmit={event=>void preview(event)}>
    <label>Staged task
     <select aria-label="Chọn staged task" value={taskId} onChange={e=>{setTaskId(e.target.value);setResult(null)}}>
      {!taskId&&<option value="">Chưa có staged task</option>}
      {tasks.map(task=><option key={task.id} value={task.id}>{task.task_kind} · {task.id.slice(0,8)}</option>)}
     </select>
    </label>
    <label>URL cần kiểm tra
     <input aria-label="URL cần kiểm tra phạm vi" type="url" maxLength={2048}
      value={url} placeholder="https://www.etsy.com/shop/..."
      onChange={e=>{setUrl(e.target.value);setResult(null)}} required/>
    </label>
    <button type="submit" disabled={loading||!taskId||!url}>Kiểm tra phạm vi</button>
   </form>
   {!tasks.length&&<p>Chưa có task staged trong danh sách gần nhất.</p>}
   {error&&<p role="alert" className="bdna-scope-error">{error}</p>}
   {result&&<div role="status" className="bdna-scope-result">
     <strong>{result.candidate_within_storefront?"URL thuộc phạm vi đường dẫn":"URL ngoài phạm vi đường dẫn"}</strong>
     <p>{labels[result.scope_reason]??"Quy tắc phạm vi chưa khớp."}</p>
     <p>Giới hạn thử nghiệm dự kiến: tối đa {result.limits.max_pages} trang, {result.limits.max_gallery_items} ảnh,
     {result.limits.max_navigation_seconds} giây/lần điều hướng, {result.limits.max_redirects} chuyển hướng.</p>
     <p>Trạng thái thực thi: <b>Đã khóa</b> · Chưa xác minh quyền marketplace.</p>
   </div>}
 </section>;
}
