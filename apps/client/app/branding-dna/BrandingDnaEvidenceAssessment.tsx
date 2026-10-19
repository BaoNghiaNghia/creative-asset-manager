import {useEffect,useState, type FormEvent} from "react";
import "./BrandingDnaEvidenceAssessment.css";

type Assessment={
  id:string;decision:string;rights_basis:string;evidence_reference:string|null;
  note:string;reviewed_by:string;created_at:string;
  assessment_grants_listing_verification:false;assessment_grants_image_rights:false;
};
const decisions=[
  {value:"needs_independent_source",label:"Cần nguồn kiểm chứng độc lập"},
  {value:"metadata_consistent",label:"Metadata hợp lý, chưa xác minh độc lập"},
  {value:"rejected",label:"Không chấp nhận hồ sơ"},
];
const rights=[
  {value:"unknown",label:"Chưa biết quyền ảnh"},
  {value:"consent_reference_recorded",label:"Có mã tài liệu đồng ý (chưa xác minh)"},
  {value:"permission_missing",label:"Chưa có giấy phép"},
];
export function BrandingDnaEvidenceAssessment({shopId,snapshotId}:{
  shopId:string;snapshotId:string;
}){
  const [history,setHistory]=useState<Assessment[]>([]);
  const [decision,setDecision]=useState("needs_independent_source");
  const [rightsBasis,setRightsBasis]=useState("unknown");
  const [reference,setReference]=useState("");
  const [note,setNote]=useState("");
  const [busy,setBusy]=useState(false);
  const [canReview,setCanReview]=useState(true);
  const [error,setError]=useState("");
  const [message,setMessage]=useState("");
  const [revision,setRevision]=useState(0);
  const endpoint="/api/v1/branding-dna/shops/"+encodeURIComponent(shopId)+
    "/listings/"+encodeURIComponent(snapshotId)+"/assessments";
  useEffect(()=>{
    const controller=new AbortController();
    setError("");setHistory([]);setCanReview(true);
    void fetch(endpoint,{credentials:"same-origin",cache:"no-store",signal:controller.signal})
      .then(async response=>{
        if(response.status===403){setCanReview(false);return {items:[] as Assessment[]};}
        if(!response.ok)throw new Error("Không đọc được lịch sử đánh giá ("+response.status+").");
        return await response.json() as {items:Assessment[]};
      }).then(result=>{if(!controller.signal.aborted)setHistory(result.items)})
      .catch(e=>{if(!controller.signal.aborted)setError(String(e))});
    return ()=>controller.abort();
  },[endpoint,revision]);
  async function submit(event:FormEvent<HTMLFormElement>){
    event.preventDefault();if(busy)return;
    setBusy(true);setError("");setMessage("");
    try{
      const response=await fetch(endpoint,{
        method:"POST",credentials:"same-origin",cache:"no-store",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({
          decision,rights_basis:rightsBasis,
          evidence_reference:rightsBasis==="consent_reference_recorded"?reference.trim():null,
          note:note.trim(),
        }),
      });
      const body=await response.json().catch(()=>null);
      if(!response.ok){
        const detail=typeof body?.detail==="string"?body.detail:"HTTP "+response.status;
        throw new Error(detail);
      }
      if(body.assessment_grants_listing_verification!==false||body.assessment_grants_image_rights!==false)
        throw new Error("API trả về trạng thái không an toàn.");
      setMessage("Đã lưu lịch sử đánh giá. Listing và quyền ảnh vẫn chưa được xác minh.");
      setRevision(x=>x+1);
    }catch(e){setError(e instanceof Error?e.message:"Không thể lưu đánh giá.");}
    finally{setBusy(false);}
  }
  return <section className="bdna-assessment" aria-label="Evidence metadata and image rights assessment">
    <h4>Đánh giá hồ sơ nguồn và quyền ảnh</h4>
    <p>Đây là biên bản của người kiểm tra, không phải giấy phép sử dụng ảnh và không chuyển dữ liệu thành verified.</p>
    {!canReview?<p>Chỉ người có quyền quản trị được xem và tạo biên bản đánh giá.</p>:
    <form onSubmit={e=>{void submit(e);}}>
      <label>Kiểm tra metadata
        <select value={decision} onChange={e=>setDecision(e.target.value)}>
          {decisions.map(x=><option key={x.value} value={x.value}>{x.label}</option>)}
        </select>
      </label>
      <label>Tình trạng tài liệu quyền ảnh
        <select value={rightsBasis} onChange={e=>setRightsBasis(e.target.value)}>
          {rights.map(x=><option key={x.value} value={x.value}>{x.label}</option>)}
        </select>
      </label>
      {rightsBasis==="consent_reference_recorded"&&<label>Mã hồ sơ đồng ý sử dụng
        <input required minLength={8} maxLength={160} value={reference}
          onChange={e=>setReference(e.target.value)} placeholder="permission-document-2026"/>
      </label>}
      <label>Ghi chú xét duyệt
        <textarea required minLength={8} maxLength={500} value={note}
          onChange={e=>setNote(e.target.value)} rows={2}
          placeholder="Mô tả nguồn kiểm tra và vấn đề còn tồn tại"/>
      </label>
      <button type="submit" disabled={busy}>{busy?"Đang lưu…":"Lưu đánh giá · Không xác minh tự động"}</button>
    </form>}
    {error&&<p role="alert" className="bdna-assessment-error">{error}</p>}
    {message&&<p role="status" className="bdna-assessment-success">{message}</p>}
    <details>
      <summary>Lịch sử đánh giá ({history.length})</summary>
      {history.length===0?<p>Chưa có biên bản đánh giá.</p>:
      <ol>{history.map(item=><li key={item.id}>
        <strong>{decisions.find(x=>x.value===item.decision)?.label??item.decision}</strong>
        <small>{item.reviewed_by} · {new Date(item.created_at).toLocaleString("vi-VN")}</small>
        <span>{rights.find(x=>x.value===item.rights_basis)?.label??item.rights_basis}</span>
        <span>{item.note}</span>
      </li>)}</ol>}
    </details>
  </section>;
}
