import {useState, type ChangeEvent} from "react";
import "./BrandingDnaEvidenceImport.css";

const MAX_JSON_BYTES = 512 * 1024;
type Preview = {
  shop_id:string;listing_key:string;source_revision:string;export_reference:string;
  content_sha256:string;image_count:number;state:"new"|"already_imported"|"revision_conflict";
  can_import:boolean;blockers:string[];rights_verified:false;scan_enabled:false;
  dispatchable:false;persisted:false;network_fetched:false;
};
const reason:Record<string,string>={
  owned_or_authorized_shop_required:"Shop chưa có quyền owned/authorized đã được cấu hình.",
  source_revision_conflict:"Revision đã tồn tại nhưng nội dung khác. Tạo revision mới cho export hợp lệ.",
};

async function postJson<T>(path:string, body:string):Promise<T>{
  const response=await fetch(path,{
    method:"POST",credentials:"same-origin",cache:"no-store",
    headers:{"Content-Type":"application/json"},body,
  });
  const data=await response.json().catch(()=>null);
  if(!response.ok){
    const detail=data?.detail;
    const message=typeof detail==="string" ? detail :
      Array.isArray(detail) ? detail.map(item=>String(item?.msg??"Dữ liệu không hợp lệ")).slice(0,3).join("; "):
      "Lỗi kiểm chứng manifest HTTP "+response.status;
    throw new Error(message);
  }
  return data as T;
}

export function BrandingDnaEvidenceImport({shopId,accessMode,onImported}:{
  shopId:string;accessMode:string;onImported:()=>void;
}){
  const [filename,setFilename]=useState("");
  const [manifest,setManifest]=useState("");
  const [preview,setPreview]=useState<Preview|null>(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const canUpload=accessMode==="owned"||accessMode==="authorized";
  const endpoint="/api/v1/branding-dna/shops/"+encodeURIComponent(shopId)+"/listings";

  async function fileChanged(event:ChangeEvent<HTMLInputElement>){
    const file=event.currentTarget.files?.[0];
    setFilename("");setManifest("");setPreview(null);setNotice("");setError("");
    if(!file)return;
    if(!file.name.toLowerCase().endsWith(".json")){
      setError("Chỉ chấp nhận file metadata JSON.");return;
    }
    if(file.size===0||file.size>MAX_JSON_BYTES){
      setError("File phải có dữ liệu và không vượt quá 512 KB.");return;
    }
    try{
      const raw=await file.text();
      const parsed:unknown=JSON.parse(raw);
      if(!parsed||Array.isArray(parsed)||typeof parsed!=="object")
        throw new Error("JSON phải chứa một object listing metadata.");
      setFilename(file.name);setManifest(raw);
    }catch(e){setError(e instanceof Error?e.message:"JSON không hợp lệ.");}
  }

  async function previewFile(){
    if(!manifest||busy)return;
    setBusy(true);setPreview(null);setError("");setNotice("");
    try{
      const result=await postJson<Preview>(endpoint+"/preview",manifest);
      if(result.shop_id!==shopId||result.scan_enabled!==false||result.persisted!==false){
        throw new Error("Phản hồi preview không đạt hợp đồng an toàn.");
      }
      setPreview(result);
    }catch(e){setError(e instanceof Error?e.message:"Không thể xem trước metadata.");}
    finally{setBusy(false);}
  }

  async function importFile(){
    if(!manifest||!preview?.can_import||preview.state!=="new"||busy)return;
    setBusy(true);setError("");setNotice("");
    try{
      const result=await postJson<{replayed:boolean;item:{title:string}}>(
        endpoint+"/import",manifest
      );
      setNotice(result.replayed
        ?"Revision đã tồn tại, không tạo dữ liệu trùng."
        :"Đã nhập metadata '"+result.item.title+"'. Chưa xác minh nguồn và chưa quét.");
      setPreview({...preview,state:"already_imported",can_import:false});
      onImported();
    }catch(e){setError(e instanceof Error?e.message:"Không thể nhập metadata.");}
    finally{setBusy(false);}
  }

  return <section className="bdna-import" aria-label="Authorized listing metadata import">
    <div className="bdna-import-heading">
      <strong>Nhập Product Evidence từ export</strong>
      <small>Chỉ metadata JSON · Không tải ảnh, không gọi web hay khởi động Scout</small>
    </div>
    <label className="bdna-import-file">
      Chọn một listing manifest JSON (≤ 512 KB)
      <input type="file" accept=".json,application/json"
        onChange={event=>{void fileChanged(event);}}/>
    </label>
    {filename&&<p className="bdna-import-file-name">Đã chọn: {filename}</p>}
    <div className="bdna-import-actions">
      <button type="button" disabled={!manifest||busy} onClick={()=>{void previewFile();}}>
        {busy?"Đang kiểm tra…":"Xem trước · Không ghi dữ liệu"}
      </button>
      {preview&&<button type="button" disabled={!preview.can_import||busy||!canUpload}
        onClick={()=>{void importFile();}}>
        Nhập metadata đã kiểm tra
      </button>}
    </div>
    {preview&&<div className="bdna-import-preview" role="status">
      <strong>{preview.listing_key} · {preview.image_count} metadata ảnh</strong>
      <span>Revision: {preview.source_revision} · Export: {preview.export_reference}</span>
      <span>Trạng thái: {preview.state==="new"?"Chưa nhập":
        preview.state==="already_imported"?"Đã có bản giống hệt":"Trùng revision khác nội dung"}</span>
      <code>SHA-256: {preview.content_sha256}</code>
      {preview.blockers.map(item=><small key={item}>{reason[item]??item}</small>)}
      <small>Quyền ảnh: chưa xác minh · Scan và dispatcher vẫn tắt.</small>
    </div>}
    {!canUpload&&<p className="bdna-import-locked">
      Shop chưa có access_mode owned/authorized. Có thể kiểm tra JSON nhưng API sẽ không cho nhập.
      Khai báo quyền hoặc ghi nhận tài liệu không tự mở quyền nhập.
    </p>}
    {error&&<p role="alert" className="bdna-evidence-error">{error}</p>}
    {notice&&<p role="status" className="bdna-evidence-stage-result">{notice}</p>}
  </section>;
}
