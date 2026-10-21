import { useEffect, useRef, useState } from "react";
import {
  catinatBatchApi, type CatinatBatchEvidence, type CatinatPhysicalReadinessItem,
} from "./api";

/** Require an explicit Vietnam UTC+07:00 offset instead of guessing browser local timezone. */
export function catinatVietnamInstant(input:string):string {
  if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(input)){
    throw new Error("Hãy nhập đầy đủ ngày và giờ Việt Nam.");
  }
  const iso=new Date(input+":00+07:00");
  if(Number.isNaN(iso.getTime())||iso.toLocaleString("sv-SE",{
    timeZone:"Asia/Ho_Chi_Minh",hour12:false,
  }).replace(" ","T").slice(0,16)!==input){
    throw new Error("Ngày hoặc giờ Việt Nam không hợp lệ.");
  }
  return iso.toISOString();
}

export function InventoryCatinatBatchEvidence({
  businessDate,sourceHash,items,
}:{
  businessDate:string;sourceHash:string;items:CatinatPhysicalReadinessItem[];
}){
  const [saved,setSaved]=useState<CatinatBatchEvidence[]>([]);
  const [selectedCell,setSelectedCell]=useState("");
  const [kind,setKind]=useState<"prepared"|"opened">("prepared");
  const [lot,setLot]=useState("");
  const [eventAt,setEventAt]=useState("");
  const [packageExpiry,setPackageExpiry]=useState("");
  const [note,setNote]=useState("");
  const [staffConfirmed,setStaffConfirmed]=useState(false);
  const [selectedWaste,setSelectedWaste]=useState("");
  const [wasteQuantity,setWasteQuantity]=useState("");
  const [wasteReason,setWasteReason]=useState("");
  const [busy,setBusy]=useState(false);
  const [message,setMessage]=useState("");
  const recordKey=useRef<string|null>(null);
  const wasteKey=useRef<string|null>(null);

  async function refresh(){
    const next=await catinatBatchApi.list(businessDate);
    if(next.source_hash!==sourceHash){
      setSaved([]);
      throw new Error("Google Sheets đã thay đổi. Cần tải lại trang kiểm kê.");
    }
    setSaved(next.items);
  }

  useEffect(()=>{
    let mounted=true;
    setSaved([]);
    setMessage("");
    setSelectedCell("");
    setSelectedWaste("");
    recordKey.current=null;wasteKey.current=null;
    catinatBatchApi.list(businessDate).then(result=>{
      if(!mounted)return;
      if(result.source_hash!==sourceHash){
        setMessage("Google Sheets đã thay đổi. Hãy kiểm tra số liệu lại.");
      }else setSaved(result.items);
    }).catch(error=>{
      if(mounted)setMessage(error instanceof Error?error.message:"Không đọc được chứng cứ lô.");
    });
    return()=>{mounted=false;};
  },[businessDate,sourceHash]);

  const cell=items.find(item=>item.sheet+"|"+item.cell===selectedCell);
  function changeRecord(setter:(value:string)=>void,value:string){
    setter(value);recordKey.current=null;setStaffConfirmed(false);
  }
  async function save(){
    if(busy||!cell||!staffConfirmed||note.trim().length<8||!lot.trim())return;
    if(!window.confirm(
      "Bạn đã trực tiếp kiểm chứng mã lô và thời điểm thực tế? "+
      "Chỉ lưu lời xác nhận của nhân viên, chưa chứng nhận hạn dùng hay thay đổi tồn."
    ))return;
    setBusy(true);setMessage("");
    try{
      if(!recordKey.current)recordKey.current=crypto.randomUUID();
      await catinatBatchApi.record({
        idempotency_key:recordKey.current,business_date:businessDate,
        expected_source_hash:sourceHash,sheet:cell.sheet,cell:cell.cell,
        lot_reference:lot.trim(),event_kind:kind,
        event_at:catinatVietnamInstant(eventAt),
        package_expiry_at:packageExpiry?catinatVietnamInstant(packageExpiry):null,
        note:note.trim(),
      });
      await refresh();
      setMessage("Đã lưu biên bản nhân viên xác nhận; hạn dùng và tồn kho CHƯA được chứng nhận.");
      setLot("");setEventAt("");setPackageExpiry("");setNote("");
      setSelectedCell("");setStaffConfirmed(false);recordKey.current=null;
    }catch(error){
      setMessage(error instanceof Error?error.message:"Chưa lưu được biên bản lô.");
    }finally{setBusy(false);}
  }

  async function requestWaste(){
    const batch=saved.find(item=>item.id===selectedWaste);
    if(busy||!batch||!batch.source_current||wasteReason.trim().length<8||!wasteQuantity)return;
    if(!window.confirm(
      "Gửi yêu cầu hủy hàng để quản lý xét duyệt? "+
      "Duyệt yêu cầu này vẫn không trừ tồn hoặc xác nhận đã hủy thực tế."
    ))return;
    setBusy(true);setMessage("");
    try{
      if(!wasteKey.current)wasteKey.current=crypto.randomUUID();
      await catinatBatchApi.requestDisposal(batch.id,{
        business_date:businessDate,expected_source_hash:sourceHash,
        idempotency_key:wasteKey.current,quantity:wasteQuantity,reason:wasteReason.trim(),
      });
      setSelectedWaste("");setWasteQuantity("");setWasteReason("");
      wasteKey.current=null;
      setMessage("Đã gửi đề nghị hủy hàng cho quản lý. Chưa hủy thực tế và chưa trừ tồn.");
    }catch(error){
      setMessage(error instanceof Error?error.message:"Chưa gửi được yêu cầu hủy hàng.");
    }finally{setBusy(false);}
  }

  return <section aria-label="Biên bản lô và đề nghị hủy Catinat">
    <h3>Biên bản lô hàng và đề nghị hủy</h3>
    <p className="inventory-muted">
      Nhân viên nhập mã lô và giờ Việt Nam chính xác sau khi kiểm tra thực tế.
      Các giá trị là <strong>lời xác nhận của nhân viên, chưa được duyệt hạn dùng.</strong>
      Không tự trừ tồn, ghi Sheets hoặc đóng ca.
    </p>
    <div className="inventory-actions" style={{flexWrap:"wrap",alignItems:"flex-end"}}>
      <label>Ô kiểm kê
        <select value={selectedCell} disabled={busy} onChange={e=>{
          setSelectedCell(e.target.value);recordKey.current=null;setStaffConfirmed(false);
        }}>
          <option value="">Chọn ô nguyên liệu</option>
          {items.map(item=><option key={item.sheet+"|"+item.cell}
            value={item.sheet+"|"+item.cell}>
            {item.sheet} · {item.cell} · {item.material} · {item.unit}
          </option>)}
        </select>
      </label>
      <label>Loại thời điểm
        <select value={kind} disabled={busy} onChange={e=>{
          setKind(e.target.value as "prepared"|"opened");recordKey.current=null;setStaffConfirmed(false);
        }}>
          <option value="prepared">Chế biến</option>
          <option value="opened">Mở bao bì</option>
        </select>
      </label>
      <label>Mã lô
        <input value={lot} maxLength={128} disabled={busy}
          placeholder="Nhập mã trên nhãn lô" onChange={e=>changeRecord(setLot,e.target.value)}/>
      </label>
      <label>Giờ chế biến/mở · Việt Nam (UTC+07:00)
        <input type="datetime-local" value={eventAt} disabled={busy}
          onChange={e=>changeRecord(setEventAt,e.target.value)}/>
      </label>
      <label>Hạn trên bao bì (nếu xác minh được)
        <input type="datetime-local" value={packageExpiry} disabled={busy}
          onChange={e=>changeRecord(setPackageExpiry,e.target.value)}/>
      </label>
    </div>
    <label>Ghi chú chứng cứ
      <textarea rows={2} value={note} disabled={busy} maxLength={1000}
        placeholder="Nêu cách xác minh mã lô và thời điểm, không suy đoán"
        onChange={e=>changeRecord(setNote,e.target.value)}/>
    </label>
    <label><input type="checkbox" checked={staffConfirmed} disabled={busy}
      onChange={e=>setStaffConfirmed(e.target.checked)}/>
      Tôi đã trực tiếp xác minh lô và thời điểm nhập ở trên, không suy đoán từ ngày trên Sheets.
    </label>
    <div className="inventory-actions">
      <button type="button" disabled={busy||!cell||!staffConfirmed||
        !lot.trim()||!eventAt||note.trim().length<8} onClick={()=>void save()}>
        Lưu lời xác nhận mã lô (không thay đổi tồn)
      </button>
      <button type="button" disabled={busy} onClick={()=>void refresh().catch(error=>
        setMessage(error instanceof Error?error.message:"Không đọc được biên bản lô."))}>
        Tải lại chứng cứ lô
      </button>
    </div>
    {message&&<p role="status" className="inventory-muted">{message}</p>}
    <details>
      <summary>Biên bản đã lưu ({saved.length})</summary>
      {saved.length?<ul>{saved.map(row=><li key={row.id}>
        {row.sheet} · {row.cell} · {row.material} · lô {row.lot_reference} ·
        {row.event_kind==="prepared"?" chế biến ":" mở bao bì "}{row.event_at}
        {" · "}{row.source_current?"Nguồn hiện tại":"Nguồn cũ — cần kiểm tra lại"}
        {" · "}Chưa chứng nhận hạn dùng/hủy hàng
      </li>)}</ul>:<p>Chưa có biên bản lô.</p>}
    </details>
    <h4>Đề nghị hủy hàng chờ quản lý xét duyệt</h4>
    <p className="inventory-muted">Yêu cầu hủy chỉ là đề xuất và không được xem là chứng từ hủy thực tế.</p>
    <div className="inventory-actions" style={{flexWrap:"wrap"}}>
      <label>Biên bản lô
        <select value={selectedWaste} disabled={busy}
          onChange={e=>{setSelectedWaste(e.target.value);wasteKey.current=null;}}>
          <option value="">Chọn biên bản còn khớp Sheets</option>
          {saved.filter(row=>row.source_current).map(row=>
            <option key={row.id} value={row.id}>{row.lot_reference} · {row.material} · {row.cell}</option>)}
        </select>
      </label>
      <label>Số lượng đề nghị hủy (đơn vị theo ô tồn)
        <input value={wasteQuantity} disabled={busy} placeholder="Ví dụ: 1.5"
          onChange={e=>{setWasteQuantity(e.target.value);wasteKey.current=null;}}/>
      </label>
    </div>
    <label>Lý do yêu cầu quản lý xác minh hủy
      <textarea rows={2} maxLength={1000} value={wasteReason} disabled={busy}
        onChange={e=>{setWasteReason(e.target.value);wasteKey.current=null;}}/>
    </label>
    <button type="button" disabled={busy||!selectedWaste||!wasteQuantity||
      wasteReason.trim().length<8} onClick={()=>void requestWaste()}>
      Gửi đề nghị hủy — chưa trừ tồn
    </button>
  </section>;
}
