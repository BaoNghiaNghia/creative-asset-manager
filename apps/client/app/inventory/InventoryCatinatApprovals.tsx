import { useCallback, useEffect, useRef, useState } from "react";
import {
  catinatApprovalApi, catinatCatalogCertificationApi, InventoryApiError,
  type CatinatCatalogCertification,
  type CatinatApproval, type CatinatApprovalKind, type CatinatApprovalStatus,
} from "./api";

const KINDS: Array<{value:CatinatApprovalKind;label:string}> = [
  {value:"discrepancy",label:"Chênh lệch kiểm kê"},
  {value:"catalog_mapping",label:"Nguyên liệu / mapping mới"},
  {value:"package_conversion",label:"Quy đổi bao bì"},
  {value:"post_close_adjustment",label:"Điều chỉnh sau chốt"},
  {value:"transfer_exception",label:"Chuyển kho chờ nhận"},
];

function vietnamDate():string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone:"Asia/Ho_Chi_Minh",year:"numeric",month:"2-digit",day:"2-digit",
  }).formatToParts(new Date());
  const part=(name:string)=>parts.find(item=>item.type===name)?.value||"";
  return [part("year"),part("month"),part("day")].join("-");
}

export function parseCatinatProposal(value:string):Record<string,unknown> {
  const parsed:unknown=JSON.parse(value);
  if (!parsed || typeof parsed!=="object" || Array.isArray(parsed) ||
      Object.keys(parsed).length===0) {
    throw new Error("Nội dung đề xuất phải là một đối tượng JSON có dữ liệu.");
  }
  return parsed as Record<string,unknown>;
}

export function exactCatinatCertificationCandidates(approval:CatinatApproval|null):
  {item_id:string;location_id:string}|null {
  if(approval?.kind!=="catalog_mapping"||approval.status!=="approved"||
     approval.proposal.matching_status!=="exact_name_and_unit_candidate")return null;
  const items=approval.proposal.candidate_item_ids;
  const places=approval.proposal.candidate_location_ids;
  if(!Array.isArray(items)||!Array.isArray(places)||
     items.length!==1||places.length!==1||
     typeof items[0]!=="string"||typeof places[0]!=="string")return null;
  return {item_id:items[0],location_id:places[0]};
}

function errorMessage(error:unknown):string {
  if (error instanceof InventoryApiError && error.status===403) {
    return "Tài khoản không có quyền thực hiện thao tác này, hoặc người tạo không được tự phê duyệt.";
  }
  return error instanceof Error?error.message:"Không thể hoàn tất yêu cầu.";
}

export function InventoryCatinatApprovals(){
  const [filter,setFilter]=useState<"all"|CatinatApprovalStatus>("pending");
  const [items,setItems]=useState<CatinatApproval[]>([]);
  const [selected,setSelected]=useState<CatinatApproval|null>(null);
  const [busy,setBusy]=useState(false);
  const [loading,setLoading]=useState(true);
  const [message,setMessage]=useState("");
  const [businessDate,setBusinessDate]=useState(vietnamDate);
  const [kind,setKind]=useState<CatinatApprovalKind>("discrepancy");
  const [subject,setSubject]=useState("");
  const [reason,setReason]=useState("");
  const [proposal,setProposal]=useState("{\n  \"material_id\": \"\",\n  \"location_id\": \"\",\n  \"physical_quantity\": \"\"\n}");
  const [decisionNote,setDecisionNote]=useState("");
  const [certificationNote,setCertificationNote]=useState("");
  const [certifications,setCertifications]=useState<CatinatCatalogCertification[]>([]);
  const requestKey=useRef<string|null>(null);

  const reload=useCallback(async()=>{
    const result=await catinatApprovalApi.list(filter==="all"?undefined:filter);
    setItems(result.items);
    setSelected(previous=>previous?result.items.find(item=>item.id===previous.id)||previous:null);
  },[filter]);

  useEffect(()=>{
    let active=true;
    setLoading(true);
    setMessage("");
    catinatApprovalApi.list(filter==="all"?undefined:filter)
      .then(result=>{if(active){setItems(result.items);setSelected(null);}})
      .catch(error=>{if(active)setMessage(errorMessage(error));})
      .finally(()=>{if(active)setLoading(false);});
    return()=>{active=false;};
  },[filter]);

  useEffect(()=>{
    setSelected(null);
    setCertificationNote("");
    setCertifications([]);
  },[businessDate]);

  const changeDraft=(setter:(value:string)=>void,value:string)=>{
    setter(value);
    requestKey.current=null;
  };

  const certificationCandidates=exactCatinatCertificationCandidates(selected);

  const loadCertifications=async()=>{
    try{
      const result=await catinatCatalogCertificationApi.list(businessDate);
      setCertifications(result.items);
    }catch(error){setMessage(errorMessage(error));}
  };

  const certifyApprovedMapping=async()=>{
    if(!selected||!certificationCandidates||busy||certificationNote.trim().length<8)return;
    if(!window.confirm(
      "Lưu chứng cứ ánh xạ đã được quản lý phê duyệt? "+
      "Việc này không kích hoạt mapping, ghi tồn hoặc chốt ca."
    ))return;
    setBusy(true);setMessage("");
    try{
      const result=await catinatCatalogCertificationApi.certify({
        approval_request_id:selected.id,
        ...certificationCandidates,note:certificationNote.trim(),
      });
      setCertifications(previous=>[result,...previous.filter(row=>row.id!==result.id)]);
      setCertificationNote("");
      setMessage("Đã lưu chứng cứ xác minh phiên bản "+result.version+
        ". Ánh xạ CHƯA được kích hoạt; tồn kho và Sheets chưa thay đổi.");
    }catch(error){setMessage(errorMessage(error));}
    finally{setBusy(false);}
  };

  const submit=async()=>{
    if(busy)return;
    setMessage("");
    try {
      const parsed=parseCatinatProposal(proposal);
      if(subject.trim().length===0||reason.trim().length<8||!businessDate){
        throw new Error("Nhập ngày, nguyên liệu/vị trí và lý do ít nhất 8 ký tự.");
      }
      // Keep the same idempotency key if a network request needs to be retried.
      if(!requestKey.current)requestKey.current=crypto.randomUUID();
      setBusy(true);
      const created=await catinatApprovalApi.create({
        idempotency_key:requestKey.current,business_date:businessDate,
        kind,subject_ref:subject.trim(),proposal:parsed,reason:reason.trim(),
      });
      requestKey.current=null;
      setMessage("Đã gửi yêu cầu. Chờ quản lý xác nhận; tồn kho chưa thay đổi.");
      setSelected(created);
      setSubject("");
      setReason("");
      await reload();
    }catch(error){setMessage(errorMessage(error));}
    finally{setBusy(false);}
  };

  const decide=async(decision:"approved"|"rejected")=>{
    if(!selected||busy)return;
    if(decisionNote.trim().length<8){
      setMessage("Nhập lý do phê duyệt hoặc từ chối, ít nhất 8 ký tự.");
      return;
    }
    if(!window.confirm(
      decision==="approved"?
        "Xác nhận phê duyệt? Việc duyệt không tự động chỉnh sửa Google Sheets.":
        "Xác nhận từ chối yêu cầu này?"
    ))return;
    setBusy(true);
    setMessage("");
    try{
      const updated=await catinatApprovalApi.decide(selected.id,decision,decisionNote.trim());
      setSelected(updated);
      setDecisionNote("");
      setMessage("Đã lưu quyết định và lịch sử kiểm toán. Chưa có số tồn nào bị ghi lại.");
      await reload();
    }catch(error){setMessage(errorMessage(error));}
    finally{setBusy(false);}
  };

  return <section className="inventory-daily">
    <h2>Ngoại lệ kiểm kê Catinat</h2>
    <p className="inventory-muted">
      Theo Q17, Q42–Q44: nhân viên gửi yêu cầu, quản lý duyệt bằng tài khoản riêng.
      Phê duyệt không tự ghi hay điều chỉnh Google Sheets.
    </p>
    <div className="inventory-actions" style={{marginBottom:16}}>
      <label htmlFor="catinat-approval-status">Trạng thái</label>
      <select id="catinat-approval-status" value={filter} onChange={event=>setFilter(event.target.value as "all"|CatinatApprovalStatus)}>
        <option value="pending">Chờ duyệt</option>
        <option value="approved">Đã duyệt</option>
        <option value="rejected">Từ chối</option>
        <option value="all">Tất cả</option>
      </select>
      <button type="button" disabled={busy||loading} onClick={()=>{setLoading(true);void reload().catch(error=>setMessage(errorMessage(error))).finally(()=>setLoading(false));}}>Làm mới</button>
    </div>
    {message&&<p role="status" className="inventory-muted">{message}</p>}
    <div className="inventory-review">
      <section>
        <h3>Danh sách yêu cầu</h3>
        {loading?<p className="inventory-muted">Đang tải…</p>:items.length===0?
          <p className="inventory-muted">Không có yêu cầu thuộc trạng thái này.</p>:
          <ul>{items.map(item=><li key={item.id}>
            <button type="button" className={selected?.id===item.id?"selected":""}
              onClick={()=>setSelected(item)}>
              <b>{KINDS.find(x=>x.value===item.kind)?.label||item.kind}</b>
              <small>{item.business_date} · {item.subject_ref} · {item.status}</small>
            </button>
          </li>)}</ul>}
      </section>
      <section className="inventory-detail">
        <h3>Chi tiết xét duyệt</h3>
        {selected?<div>
          <p><b>Người yêu cầu:</b> {selected.requested_by}</p>
          <p><b>Ngày:</b> {selected.business_date}</p>
          <p><b>Lý do:</b> {selected.reason}</p>
          <p><b>Trạng thái:</b> {selected.status}</p>
          <pre style={{maxHeight:240,overflow:"auto",whiteSpace:"pre-wrap"}}>
            {JSON.stringify(selected.proposal,null,2)}
          </pre>
          {selected.decided_by&&<p><b>Người duyệt:</b> {selected.decided_by} · {selected.decision_reason}</p>}
          {certificationCandidates&&<section className="inventory-material-candidate">
            <h4>Lưu chứng cứ ánh xạ đã duyệt (không kích hoạt)</h4>
            <p className="inventory-muted">
              Nguyên liệu ID: {certificationCandidates.item_id}, kho ID: {certificationCandidates.location_id}.
              Hệ thống sẽ đối chiếu lại dữ liệu Sheets và danh mục trước khi ghi chứng cứ.
              <strong> Chứng cứ không thay đổi số tồn, không chốt ca hoặc tự ghi Sheets.</strong>
            </p>
            <label htmlFor="catinat-cert-note">Ghi chú xác minh của quản lý (ít nhất 8 ký tự)</label>
            <textarea id="catinat-cert-note" rows={2} value={certificationNote}
              onChange={event=>setCertificationNote(event.target.value)}/>
            <button type="button" disabled={busy||certificationNote.trim().length<8}
              onClick={()=>void certifyApprovedMapping()}>
              Lưu chứng cứ đã xác minh
            </button>
          </section>}

          {selected.status==="pending"&&<>
            <label htmlFor="catinat-decision-note">Lý do quyết định (bắt buộc)</label>
            <textarea id="catinat-decision-note" rows={3} value={decisionNote} onChange={event=>setDecisionNote(event.target.value)}/>
            <div className="inventory-actions">
              <button type="button" disabled={busy} onClick={()=>void decide("approved")}>Quản lý duyệt</button>
              <button type="button" disabled={busy} className="danger" onClick={()=>void decide("rejected")}>Từ chối</button>
            </div>
          </>}
        </div>:<p className="inventory-muted">Chọn một yêu cầu để xem chi tiết.</p>}
      </section>
    </div>
    <section style={{marginTop:20}}>
      <h3>Lịch sử chứng cứ ánh xạ (chưa kích hoạt)</h3>
      <button type="button" disabled={busy} onClick={()=>void loadCertifications()}>
        Xem chứng cứ ngày {businessDate}
      </button>
      {certifications.map(record=><p key={record.id} className="inventory-muted">
        v{record.version} · {record.sheet} {record.cell} · {record.material} ·
        {record.item_id} / {record.location_id} ·
        {record.certified_by} · Chỉ lưu chứng cứ, chưa cập nhật tồn
      </p>)}
    </section>
    <section style={{marginTop:20}}>
      <h3>Gửi yêu cầu mới</h3>
      <p className="inventory-muted">Số liệu bên dưới là đề xuất cần duyệt, không phải tồn kho đã xác nhận.</p>
      <div className="inventory-actions" style={{alignItems:"flex-end",flexWrap:"wrap"}}>
        <label htmlFor="catinat-business-date">Ngày kinh doanh
          <input id="catinat-business-date" type="date" value={businessDate} onChange={event=>changeDraft(setBusinessDate,event.target.value)}/>
        </label>
        <label htmlFor="catinat-kind">Loại yêu cầu
          <select id="catinat-kind" value={kind} onChange={event=>{setKind(event.target.value as CatinatApprovalKind);requestKey.current=null;}}>
            {KINDS.map(option=><option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
        </label>
        <label htmlFor="catinat-subject">Nguyên liệu / vị trí / giao dịch
          <input id="catinat-subject" value={subject} onChange={event=>changeDraft(setSubject,event.target.value)} placeholder="VD: bar:milk"/>
        </label>
      </div>
      <label htmlFor="catinat-reason">Lý do (bắt buộc)</label>
      <textarea id="catinat-reason" rows={2} value={reason} onChange={event=>changeDraft(setReason,event.target.value)}/>
      <label htmlFor="catinat-proposal">Thông tin bằng chứng / đề xuất dạng JSON</label>
      <textarea id="catinat-proposal" rows={6} value={proposal} onChange={event=>changeDraft(setProposal,event.target.value)}/>
      <div className="inventory-actions">
        <button type="button" disabled={busy} onClick={()=>void submit()}>{busy?"Đang xử lý…":"Gửi quản lý duyệt"}</button>
      </div>
    </section>
  </section>;
}
