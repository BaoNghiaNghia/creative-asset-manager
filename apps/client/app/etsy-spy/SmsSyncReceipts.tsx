import { useState } from "react";
import { createPortal } from "react-dom";
import { spyFetch } from "./api";
import "./SmsSyncReceipts.css";

const SHOP = "Tiktok_McGowanshopdesigns";
const SCHEMA = "cam-sms-sync-v3-operator-receipts-v1";
const ID = /^[0-9]{5,20}$/;
const SHA = /^[a-f0-9]{64}$/;
const UUID = /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i;
const DRIVE = /^https:\/\/drive\.google\.com\/file\/d\/[A-Za-z0-9_-]{10,128}\/view(?:\?resourcekey=[A-Za-z0-9_-]{10,128})?$/;
type Receipt = {
  confirm: true; candidate_id: string; idea_code: string; idea_id: string;
  target_id: string; shop: string; reference_images: 2; target_videos: 1;
  listings_made: 0; listings_live: 0; etsy_id: string; vertical_field_url: string;
};
type Ack = { state: string; live_sms_verified_by_cam: boolean; target_video_identity_verified: boolean;
  record: Omit<Receipt,"confirm"> & { reported_at: string }; changed: boolean };
const fields = ["confirm","candidate_id","idea_code","idea_id","target_id","shop","reference_images","target_videos","listings_made","listings_live","etsy_id","vertical_field_url"] as const;
const isObject = (v: unknown): v is Record<string,unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const matches = (v: unknown, pattern: RegExp): boolean => typeof v === "string" && pattern.test(v);
export function parseReceiptBundle(value: unknown): Receipt[] {
  if (!isObject(value) || value.schema !== SCHEMA || value.shop !== SHOP ||
      !Array.isArray(value.receipts) || !value.receipts.length || value.receipts.length > 50)
    throw Error("Gói receipt SMS không hợp lệ (1–50 bản ghi).");
  const out: Receipt[] = [];
  for (const raw of value.receipts) {
    if (!isObject(raw) || Object.keys(raw).length !== fields.length || !fields.every(k => Object.hasOwn(raw,k)) ||
      raw.confirm !== true || !matches(raw.etsy_id,ID) || !matches(raw.candidate_id,SHA) ||
      !matches(raw.idea_id,UUID) || !matches(raw.target_id,UUID) || !matches(raw.idea_code,/^I[0-9]+$/) ||
      raw.shop !== SHOP || raw.reference_images !== 2 || raw.target_videos !== 1 ||
      raw.listings_made !== 0 || raw.listings_live !== 0 || !matches(raw.vertical_field_url,DRIVE))
      throw Error("Receipt sai định dạng, shop hoặc số lượng ảnh/video.");
    const r = raw as Receipt;
    if(out.some(p=>p.etsy_id===r.etsy_id||p.idea_id===r.idea_id||p.idea_code===r.idea_code||p.target_id===r.target_id))
      throw Error("Gói receipt có định danh trùng.");
    out.push(r);
  }
  return out;
}
export async function importSmsReceipts(rows: Receipt[], onAck: (ack: Ack)=>void, fetcher=spyFetch): Promise<void> {
  for (const row of rows) {
    const ack = await fetcher<Ack>("/listings/"+encodeURIComponent(row.etsy_id)+"/sms-sync-readback",
      {method:"POST",body:JSON.stringify(row)});
    if (ack?.state !== "reported_counts_match" || ack.live_sms_verified_by_cam !== false ||
        ack.target_video_identity_verified !== false ||
        !ack.record || fields.some(k=>k!=="confirm" && ack.record[k]!==row[k]) ||
        !Number.isFinite(Date.parse(ack.record.reported_at)))
      throw Error("Receipt trả về không khớp: "+row.idea_code+". Dừng để đối soát.");
    onAck(ack);
  }
}
type Stage = {
  state: "prepared" | "not_prepared"; schema: string; shop: string;
  observed_at?: string; sms_observed_count?: number; recorded_count?: number;
  pending_count?: number; review_count?: number; held_count?: number;
  quota?: { state: string; limit?: number }; receipts: Receipt[];
};
export function parseStage(value: unknown): Stage {
  if(!isObject(value)||value.schema!==SCHEMA||value.shop!==SHOP||
    value.live_sms_verified_by_cam!==false)throw Error("Không thể đọc trạng thái SMS.");
  if(value.state==="not_prepared")return {state:"not_prepared",schema:SCHEMA,shop:SHOP,receipts:[]};
  const counts=["sms_observed_count","recorded_count","pending_count","review_count","held_count"];
  if(value.state!=="prepared"||counts.some(k=>typeof value[k]!=="number"||!Number.isInteger(value[k])||Number(value[k])<0||Number(value[k])>50)||
    Number(value.recorded_count)+Number(value.pending_count)+Number(value.review_count)!==Number(value.sms_observed_count)||
    !Array.isArray(value.receipts)||value.receipts.length!==value.pending_count||
    !isObject(value.quota)||!["UNKNOWN","SMS_DAILY_LIMIT","REVIEW_AFTER_LIMIT"].includes(String(value.quota.state))||
    typeof value.observed_at!=="string"||!Number.isFinite(Date.parse(value.observed_at)))
    throw Error("Trạng thái SMS chưa hợp lệ.");
  const receipts=value.receipts.length?parseReceiptBundle(value):[];
  return {...value,receipts} as Stage;
}
export function SmsSyncReceipts({canManage}:{canManage:boolean}) {
  const [open,setOpen]=useState(false),[stage,setStage]=useState<Stage|null>(null);
  const [loading,setLoading]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState("");
  const [progress,setProgress]=useState(0);
  if(!canManage)return null;
  const refresh=async()=>{
    setLoading(true);setError("");setStage(null);
    try{setStage(parseStage(await spyFetch<unknown>("/sms-sync-stage")));}
    catch{setError("Không tải được trạng thái SMS. Thử lại.");}
    finally{setLoading(false);}
  };
  const toggle=()=>{
    if(busy)return;
    if(open){setOpen(false);return;}
    setOpen(true);setProgress(0);void refresh();
  };
  const submit=async()=>{
    if(!canManage||busy||!stage||stage.state!=="prepared"||!stage.receipts.length)return;
    setBusy(true);setError("");setProgress(0);
    let done=0;
    try{
      await importSmsReceipts(stage.receipts,()=>{done++;setProgress(done);});
      await refresh();
    }catch{
      setError("Đã ghi "+done+"/"+stage.receipts.length+". Có mục cần kiểm tra. Không gửi lại SMS.");
      // Never blindly retry a partially completed write. Reload before next action.
      setStage(null);
    }finally{setBusy(false);}
  };
  return <div className="cam-sms-sync-receipts">
    <button className="etsy-btn cam-sms-sync-trigger" type="button" onClick={toggle} aria-expanded={open} disabled={busy}>SMS Sync{stage?.state==="prepared"?" · "+stage.sms_observed_count:""}</button>
    {open&&createPortal(<section className="cam-sms-sync-panel" role="dialog" aria-label="SMS Sync">
      <div className="cam-sms-sync-heading"><strong>SMS Sync</strong><button type="button" disabled={busy} onClick={()=>setOpen(false)} aria-label="Đóng SMS Sync">×</button></div>
      {loading&&<p role="status">Đang kiểm tra…</p>}
      {stage?.state==="prepared"&&<>
        <div className="cam-sms-sync-metrics">
          <div><strong>{stage.sms_observed_count}</strong><span>Đã gửi SMS</span></div>
          <div><strong>{stage.held_count}</strong><span>Chờ gửi</span></div>
          <div><strong>{busy?Math.max(0,(stage.pending_count??0)-progress):stage.pending_count}</strong><span>Chưa ghi CAM</span></div>
        </div>
        {stage.quota?.state==="SMS_DAILY_LIMIT"&&<p className="cam-sms-sync-quota">SMS báo hết lượt tạo qua AI (20/ngày).</p>}
        {!!stage.review_count&&<p role="status">{stage.review_count} sản phẩm cần kiểm tra lại.</p>}
        {!!stage.pending_count&&<>
          <button type="button" className="cam-sms-sync-primary" disabled={busy||loading} onClick={()=>void submit()}>{busy?"Đang ghi "+progress+"/"+stage.pending_count+"…":"Ghi nhận "+stage.pending_count+" sản phẩm đã gửi"}</button>
          <small>Chỉ cập nhật CAM, không gửi lại SMS.</small>
        </>}
        {!stage.pending_count&&!stage.review_count&&<p className="cam-sms-sync-done">CAM đã ghi nhận toàn bộ sản phẩm đã gửi.</p>}
        <small>Đối soát: {new Date(stage.observed_at!).toLocaleString("vi-VN",{dateStyle:"short",timeStyle:"short",timeZone:"Asia/Ho_Chi_Minh"})}</small>
      </>}
      {stage?.state==="not_prepared"&&<p>Chưa có đợt đối soát được chuẩn bị.</p>}
      {error&&<p role="alert">{error}</p>}
      <div className="cam-sms-sync-footer">
        <a href="https://sms.mbczone.com/stores/listing-ideas" target="_blank" rel="noopener noreferrer">Mở SMS ↗</a>
        <button type="button" disabled={busy||loading} onClick={()=>void refresh()}>Làm mới</button>
      </div>
    </section>,document.body)}
  </div>;
}
