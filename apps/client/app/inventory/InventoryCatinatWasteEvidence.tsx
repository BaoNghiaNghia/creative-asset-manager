import {useEffect,useRef,useState} from "react";
import {catinatApprovalApi,catinatWasteEvidenceApi,
  type CatinatApproval,type CatinatWasteEvidence} from "./api";
import {catinatVietnamInstant} from "./InventoryCatinatBatchEvidence";

/** Separate human witness and manager countersign, never a stock posting UI. */
export function InventoryCatinatWasteEvidence({
  businessDate,sourceHash,
}:{businessDate:string;sourceHash:string}){
  const [approvals,setApprovals]=useState<CatinatApproval[]>([]);
  const [witnesses,setWitnesses]=useState<CatinatWasteEvidence[]>([]);
  const [requestId,setRequestId]=useState("");
  const [observedAt,setObservedAt]=useState("");
  const [method,setMethod]=useState("");
  const [reference,setReference]=useState("");
  const [note,setNote]=useState("");
  const [witnessAgreed,setWitnessAgreed]=useState(false);
  const [certId,setCertId]=useState("");
  const [certNote,setCertNote]=useState("");
  const [busy,setBusy]=useState(false);
  const [message,setMessage]=useState("");
  const witnessKey=useRef<string|null>(null);
  const currentApproval=approvals.find(row=>row.id===requestId);
  const approved=approvals.filter(row=>
    row.kind==="waste_disposal"&&row.status==="approved"&&
    row.proposal.source_hash===sourceHash);
  const pendingCert=witnesses.filter(row=>row.source_current&&
    !row.physical_evidence_countersigned);

  async function refresh(){
    const [reviews,evidence]=await Promise.all([
      catinatApprovalApi.list("approved"),
      catinatWasteEvidenceApi.list(businessDate),
    ]);
    if(evidence.source_hash!==sourceHash){
      setWitnesses([]);setApprovals([]);
      throw new Error("Nguồn Google Sheets đã thay đổi, hãy kiểm kê lại.");
    }
    setApprovals(reviews.items);setWitnesses(evidence.items);
  }
  useEffect(()=>{
    let active=true;
    setApprovals([]);setWitnesses([]);setMessage("");
    setRequestId("");setCertId("");
    witnessKey.current=null;
    Promise.all([catinatApprovalApi.list("approved"),
      catinatWasteEvidenceApi.list(businessDate)]).then(([reviews,evidence])=>{
        if(!active)return;
        if(evidence.source_hash!==sourceHash){
          setMessage("Nguồn Sheets đã thay đổi, hãy tải lại bảng kiểm kê.");
        }else{
          setApprovals(reviews.items);setWitnesses(evidence.items);
        }
      }).catch(err=>{if(active)setMessage(err instanceof Error?err.message:"Không đọc được biên bản hủy.");});
    return()=>{active=false;};
  },[businessDate,sourceHash]);

  async function submitWitness(){
    if(!currentApproval||!witnessAgreed||busy||!observedAt||
      method.trim().length<8||reference.trim().length<8||note.trim().length<8)return;
    if(!window.confirm("Bạn có trực tiếp chứng kiến hủy thực tế và có chứng từ? "+
      "Chỉ ghi nhận lời khai nhân viên; không trừ tồn."))return;
    setBusy(true);setMessage("");
    try{
      if(!witnessKey.current)witnessKey.current=crypto.randomUUID();
      await catinatWasteEvidenceApi.witness({
        approval_request_id:currentApproval.id,business_date:businessDate,
        expected_source_hash:sourceHash,idempotency_key:witnessKey.current,
        quantity:String(currentApproval.proposal.quantity_requested),
        observed_at:catinatVietnamInstant(observedAt),
        method:method.trim(),evidence_reference:reference.trim(),note:note.trim(),
      });
      await refresh();
      setRequestId("");setObservedAt("");setMethod("");setReference("");setNote("");
      setWitnessAgreed(false);witnessKey.current=null;
      setMessage("Đã lưu lời xác nhận chứng kiến hủy, đang chờ quản lý đối chiếu. Tồn không đổi.");
    }catch(error){setMessage(error instanceof Error?error.message:"Chưa ghi được chứng cứ.");}
    finally{setBusy(false);}
  }

  async function submitManager(){
    if(!certId||busy||certNote.trim().length<8)return;
    if(!window.confirm("Bạn đã tự kiểm tra biên bản nhân viên và tài liệu tham chiếu? "+
      "Ký xác nhận vẫn không hạch toán số tồn."))return;
    setBusy(true);setMessage("");
    try{
      await catinatWasteEvidenceApi.countersign(certId,{
        business_date:businessDate,expected_source_hash:sourceHash,
        note:certNote.trim(),
      });
      await refresh();
      setCertId("");setCertNote("");
      setMessage("Đã ghi chữ ký quản lý cho chứng cứ hủy. Sổ tồn và Sheets chưa được thay đổi.");
    }catch(error){setMessage(error instanceof Error?error.message:"Không ký được chứng cứ hủy.");}
    finally{setBusy(false);}
  }

  return <section aria-label="Xác nhận hủy thực tế và ký chứng từ Catinat">
    <h3>Biên bản hủy có người chứng kiến và quản lý ký</h3>
    <p className="inventory-muted">Yêu cầu hủy phải được quản lý duyệt trước.
      Người chứng kiến phải khác người đề nghị, người lập biên bản lô và quản lý phê duyệt.
      <strong> Hai chữ ký không ghi sổ tồn, không cập nhật Sheets và không tự chốt ca.</strong>
    </p>
    <div className="inventory-actions" style={{flexWrap:"wrap",alignItems:"flex-end"}}>
      <label>Đề nghị hủy đã được duyệt
        <select disabled={busy} value={requestId}
          onChange={e=>{setRequestId(e.target.value);witnessKey.current=null;setWitnessAgreed(false);}}>
          <option value="">Chọn yêu cầu đúng lô</option>
          {approved.map(row=><option key={row.id} value={row.id}>
            {row.proposal.lot_reference as string} · {row.proposal.material as string} ·
            {" "}{String(row.proposal.quantity_requested)} {String(row.proposal.unit)}
          </option>)}
        </select>
      </label>
      <label>Thời điểm chứng kiến hủy · giờ Việt Nam
        <input type="datetime-local" value={observedAt} disabled={busy}
          onChange={e=>{setObservedAt(e.target.value);witnessKey.current=null;setWitnessAgreed(false);}}/>
      </label>
      <label>Phương pháp xử lý
        <input value={method} disabled={busy} maxLength={100}
          onChange={e=>{setMethod(e.target.value);witnessKey.current=null;setWitnessAgreed(false);}}
          placeholder="Đã thu hồi và tiêu hủy tại..." />
      </label>
      <label>Mã chứng từ / tệp Drive
        <input value={reference} disabled={busy} maxLength={2048}
          onChange={e=>{setReference(e.target.value);witnessKey.current=null;setWitnessAgreed(false);}}
          placeholder="Tham chiếu bằng chứng thực tế" />
      </label>
    </div>
    <label>Ghi chú người trực tiếp chứng kiến
      <textarea rows={2} maxLength={1000} value={note} disabled={busy}
        onChange={e=>{setNote(e.target.value);witnessKey.current=null;setWitnessAgreed(false);}}/>
    </label>
    <label><input type="checkbox" disabled={busy} checked={witnessAgreed}
      onChange={e=>setWitnessAgreed(e.target.checked)}/>
      Tôi đã trực tiếp chứng kiến việc hủy và đối chiếu chứng từ thực tế.
    </label>
    <button type="button" onClick={()=>void submitWitness()}
      disabled={busy||!currentApproval||!witnessAgreed||!observedAt||
        method.trim().length<8||reference.trim().length<8||note.trim().length<8}>
      Lưu biên bản người chứng kiến — chưa trừ tồn
    </button>
    <h4>Quản lý ký xác nhận chứng cứ</h4>
    <div className="inventory-actions" style={{flexWrap:"wrap"}}>
      <label>Biên bản chờ ký
        <select value={certId} disabled={busy} onChange={e=>setCertId(e.target.value)}>
          <option value="">Chọn biên bản cần đối chiếu</option>
          {pendingCert.map(row=><option key={row.id} value={row.id}>
            {row.id} · {row.quantity} {row.unit} · {row.witnessed_by}
          </option>)}
        </select>
      </label>
      <label>Ghi chú quản lý sau khi kiểm tra
        <textarea rows={2} value={certNote} maxLength={1000} disabled={busy}
          onChange={e=>setCertNote(e.target.value)}/>
      </label>
    </div>
    <button type="button" disabled={busy||!certId||certNote.trim().length<8}
      onClick={()=>void submitManager()}>
      Ký chứng cứ hủy — chưa hạch toán tồn
    </button>
    {message&&<p role="status" className="inventory-muted">{message}</p>}
    <details>
      <summary>Danh sách chứng cứ hủy ({witnesses.length})</summary>
      <ul>{witnesses.map(w=><li key={w.id}>
        {w.quantity} {w.unit} · {w.witnessed_by} · {w.observed_at} ·
        {w.physical_evidence_countersigned?
          " Quản lý đã ký "+w.manager_certified_by:" Chờ quản lý xác nhận"}
        {" · "}{w.source_current?"Nguồn hiện tại":"Nguồn cũ"}
        {" · "}Chưa ghi sổ và chưa trừ tồn
      </li>)}</ul>
    </details>
  </section>;
}
