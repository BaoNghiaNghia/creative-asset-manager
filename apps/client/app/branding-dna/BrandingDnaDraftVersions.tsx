import {useEffect,useState, type FormEvent} from "react";
import "./BrandingDnaDraftVersions.css";

type Review={id:string;decision:string;note:string;reviewed_by:string;created_at:string};
type Version={
  id:string;version_number:number;input_fingerprint:string;content_sha256:string;
  created_by:string;created_at:string;status:"current"|"stale";
  review:Review|null;is_final_brand_dna:false;
  dna_generation_enabled:false;dispatchable:false;
};
type History={items:Version[];count:number;final_brand_dna_enabled:false};
const choices=[
  {key:"needs_changes",name:"Yêu cầu sửa đề cương"},
  {key:"fact_outline_reviewed",name:"Đã xem dữ kiện (chưa duyệt DNA)"},
  {key:"rejected",name:"Từ chối đề cương"}
];
async function request<T>(url:string, init?:RequestInit):Promise<T>{
  const response=await fetch(url,{
    credentials:"same-origin",cache:"no-store",...init,
  });
  const result=await response.json().catch(()=>null);
  if(!response.ok){
    throw new Error(typeof result?.detail==="string"
      ?result.detail:"Lỗi phiên bản DNA ("+response.status+")");
  }
  return result as T;
}

export function BrandingDnaDraftVersions({shopId,previewFingerprint,previewReady}:{
  shopId:string;previewFingerprint?:string;previewReady:boolean;
}){
  const [history,setHistory]=useState<History|null>(null);
  const [loading,setLoading]=useState(false);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const [reviewId,setReviewId]=useState<string|null>(null);
  const [reviewDecision,setReviewDecision]=useState("needs_changes");
  const [reviewNote,setReviewNote]=useState("");
  const base="/api/v1/branding-dna/shops/"+encodeURIComponent(shopId)+"/dna-draft/versions";
  async function refresh(){
    const result=await request<History>(base);
    setHistory(result);
  }
  useEffect(()=>{
    let active=true;
    setHistory(null);setLoading(true);setError("");setNotice("");
    void request<History>(base).then(value=>{if(active)setHistory(value)})
      .catch(e=>{if(active)setError(String(e))})
      .finally(()=>{if(active)setLoading(false)});
    return ()=>{active=false};
  },[base]);
  async function save(){
    if(!previewFingerprint||!previewReady||busy)return;
    setBusy(true);setError("");setNotice("");
    try{
      const result=await request<Version & {replayed:boolean}>(base,{
        method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({input_fingerprint:previewFingerprint})
      });
      if(result.is_final_brand_dna!==false||result.dna_generation_enabled!==false){
        throw new Error("Server returned an unsafe version state");
      }
      setNotice(result.replayed?"Bản này đã được lưu trước đó."
        :"Đã lưu đề cương dữ kiện có phiên bản, chưa phát hành Brand DNA.");
      await refresh();
    }catch(e){setError(e instanceof Error?e.message:"Không lưu được phiên bản.");}
    finally{setBusy(false);}
  }
  async function submitReview(e:FormEvent<HTMLFormElement>,versionId:string){
    e.preventDefault();if(busy)return;
    setBusy(true);setError("");setNotice("");
    try{
      await request(base+"/"+encodeURIComponent(versionId)+"/reviews",{
        method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({decision:reviewDecision,note:reviewNote.trim()})
      });
      setReviewId(null);setReviewNote("");
      setNotice("Đã ghi nhận đánh giá đề cương. Không cấp phép ảnh hoặc phát hành DNA.");
      await refresh();
    }catch(e){setError(e instanceof Error?e.message:"Không thể đánh giá.");}
    finally{setBusy(false);}
  }
  return <section className="bdna-version" aria-label="Versioned Brand DNA fact outlines">
    <div className="bdna-version-head">
      <div><strong>Phiên bản đề cương DNA</strong>
        <p>Chỉ lưu dữ kiện có dẫn chứng. Người khác người tạo phải xét duyệt.
          Không sinh AI, không tạo DNA chính thức.</p></div>
      <button type="button" disabled={!previewReady||!previewFingerprint||busy}
        onClick={()=>{void save();}}>
        {busy?"Đang xử lý…":"Lưu phiên bản từ preview"}
      </button>
    </div>
    {!previewReady&&<p className="bdna-version-blocked">Chưa đủ listing đã xác minh để lưu phiên bản mới.</p>}
    {error&&<p className="bdna-version-error" role="alert">{error}</p>}
    {notice&&<p className="bdna-version-notice" role="status">{notice}</p>}
    {loading?<p>Đang tải lịch sử phiên bản…</p>:
      history?.items.length ? <div className="bdna-version-list">
        {history.items.map(v=><div className="bdna-version-item" key={v.id}>
          <div className="bdna-version-row">
            <strong>v{v.version_number} · {v.status==="current"?"Nguồn hiện tại":"Nguồn đã thay đổi"}</strong>
            <small>{new Date(v.created_at).toLocaleString("vi-VN")} · {v.created_by}</small>
          </div>
          <code>SHA-256: {v.content_sha256}</code>
          <p>{v.review
            ? "Lần xét gần nhất: "+(choices.find(x=>x.key===v.review?.decision)?.name??v.review.decision)
              +" · "+v.review.reviewed_by
            : "Chưa có đánh giá đề cương."}</p>
          <button type="button" disabled={v.status!=="current"||busy}
            onClick={()=>{setReviewId(reviewId===v.id?null:v.id);
              setReviewDecision("needs_changes");setReviewNote("");}}>
            {reviewId===v.id?"Đóng":"Đánh giá phiên bản"}
          </button>
          {reviewId===v.id&&<form onSubmit={e=>{void submitReview(e,v.id);}}>
            <label>Kết quả xét đề cương
              <select value={reviewDecision} onChange={e=>setReviewDecision(e.target.value)}>
                {choices.map(c=><option value={c.key} key={c.key}>{c.name}</option>)}
              </select>
            </label>
            <label>Ghi chú có dẫn chứng
              <textarea required minLength={8} maxLength={500} rows={2}
                value={reviewNote} onChange={e=>setReviewNote(e.target.value)}/>
            </label>
            <small>Không thể tự duyệt bản mình tạo; kết quả không phát hành Brand DNA.</small>
            <button disabled={busy} type="submit">Lưu nhận xét</button>
          </form>}
        </div>)}
      </div>:<p className="bdna-version-blocked">Chưa có phiên bản đề cương nào.</p>}
    {history&&history.count>history.items.length&&
      <small>Đang hiển thị {history.items.length}/{history.count} phiên bản mới nhất.</small>}
  </section>;
}
