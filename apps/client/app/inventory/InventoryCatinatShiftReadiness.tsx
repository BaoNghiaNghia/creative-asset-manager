import { useEffect, useRef, useState } from "react";
import {
  catinatShiftApi, catinatApprovalApi, InventoryApiError, type CatinatEveningReadiness, type CatinatPhysicalReadinessItem,
  type CatinatCountConfirmationState, type CatinatClosePreflight, type CatinatCatalogMapping, type CatinatCertificationCoverage, type CatinatPackageConversionReview, type CatinatSafetyTriage,
} from "./api";

function todayVietnam(){
  const pieces=new Intl.DateTimeFormat("en-CA",{
    timeZone:"Asia/Ho_Chi_Minh",year:"numeric",month:"2-digit",day:"2-digit",
  }).formatToParts(new Date());
  const value=(kind:string)=>pieces.find(part=>part.type===kind)?.value||"";
  return [value("year"),value("month"),value("day")].join("-");
}

export function summarizeCatinatReadiness(items:CatinatPhysicalReadinessItem[]) {
  const bySheet:Record<string,{total:number;missing:number;present:number}>={};
  for(const item of items){
    const summary=bySheet[item.sheet]||(bySheet[item.sheet]={total:0,missing:0,present:0});
    summary.total++;
    if(item.quantity===null)summary.missing++;
    else summary.present++;
  }
  return bySheet;
}

const ISSUE:Record<string,string>={
  physical_count_missing_or_ambiguous:"Ô tồn cuối trống hoặc số lượng không rõ",
  employee_physical_confirmation_required:"Có số lượng nhưng chưa được nhân viên xác nhận",
  prepared_time_and_expiry_require_staff_verification:"Mẻ có ngày làm nhưng thiếu giờ/hạn được xác minh",
  unmapped_quantity_row:"Có dữ liệu ở dòng chưa xác định nguyên liệu",
  stock_unit_missing:"Thiếu đơn vị của phần tồn kho",
  no_physical_counts_discovered:"Chưa tìm thấy tồn cuối để kiểm kê",
  inventory_items_missing_for_tenant:"Chưa cấu hình nguyên liệu hoạt động cho cửa hàng",
  inventory_locations_missing_for_tenant:"Chưa cấu hình kho/vị trí hoạt động cho cửa hàng",
  catalog_item_and_location_mapping_not_certified:"Chưa xác minh đối chiếu nguyên liệu và kho với Google Sheets",
  batch_expiration_and_waste_reconciliation_not_certified:"Chưa đối chiếu hạn sử dụng và hủy nguyên liệu",
  shift_reconciliation_and_transfer_posting_not_certified:"Chưa đối soát bàn giao ca và ghi sổ chuyển kho",
  concurrent_google_sheet_write_protocol_not_certified:"Chưa có cơ chế khóa/đối chiếu khi nhiều người sửa Sheets",
  physical_count_unconfirmed_or_stale:"Kiểm đếm chưa xác nhận hoặc Sheets đã thay đổi",
};

export const CATINAT_MAPPING_STATUS:Record<string,string>={
  exact_name_and_unit_candidate:"Tên, đơn vị và tên kho trùng khớp; chờ quản lý xác nhận",
  no_exact_item:"Chưa tìm thấy nguyên liệu cùng tên trong danh mục",
  ambiguous_item:"Nhiều nguyên liệu trùng tên; cần quản lý phân biệt",
  material_name_missing:"Dòng Sheets chưa có tên nguyên liệu",
  source_unit_missing:"Thiếu đơn vị tồn kho trong Sheets",
  unit_mismatch:"Đơn vị Sheets khác danh mục; chưa xác nhận quy đổi",
  location_requires_review:"Khu vực kho chưa có ánh xạ rõ ràng",
  no_exact_location:"Chưa tìm thấy vị trí kho cùng tên",
  ambiguous_location:"Nhiều vị trí kho trùng tên; cần phân biệt",
  exact_name_candidate:"Tên kho trùng khớp; chưa được phê duyệt",
};

function itemKey(sheet:string,cell:string){return sheet+"|"+cell;}

export function buildCatinatMappingReviewProposal(
  mapping:CatinatCatalogMapping, row:CatinatCatalogMapping["items"][number],
):Record<string,unknown> {
  const valid=mapping.items.find(candidate=>
    candidate.sheet===row.sheet&&candidate.cell===row.cell);
  const location=mapping.locations.find(candidate=>candidate.sheet===row.sheet);
  if(!valid || !location || JSON.stringify(valid)!==JSON.stringify(row)){
    throw new Error("Ứng viên ánh xạ không còn khớp. Hãy tải lại Google Sheets.");
  }
  return {
    source_hash:mapping.source_hash,
    source_sheet:row.sheet,source_cell:row.cell,
    material_display:row.material,source_form:row.form,source_unit:row.unit,
    matching_status:row.status,
    candidate_item_ids:[...row.candidate_item_ids],
    candidate_location_ids:[...location.candidate_location_ids],
    mapping_certified:false,inventory_write_performed:false,sheets_writes:0,
  };
}


export function InventoryCatinatShiftReadiness(){
  const [businessDate,setBusinessDate]=useState(todayVietnam);
  const [data,setData]=useState<CatinatEveningReadiness|null>(null);
  const [confirmed,setConfirmed]=useState<CatinatCountConfirmationState|null>(null);
  const [preflight,setPreflight]=useState<CatinatClosePreflight|null>(null);
  const [catalog,setCatalog]=useState<CatinatCatalogMapping|null>(null);
  const [coverage,setCoverage]=useState<CatinatCertificationCoverage|null>(null);
  const [conversions,setConversions]=useState<CatinatPackageConversionReview|null>(null);
  const [triage,setTriage]=useState<CatinatSafetyTriage|null>(null);
  const [selected,setSelected]=useState<string[]>([]);
  const [busy,setBusy]=useState(false);
  const [message,setMessage]=useState("");
  const [staffChecked,setStaffChecked]=useState(false);
  const [mappingDraft,setMappingDraft]=useState<CatinatCatalogMapping["items"][number]|null>(null);
  const [mappingReason,setMappingReason]=useState("");
  const reviewKey=useRef<string|null>(null);

  useEffect(()=>{
    setData(null);
    setConfirmed(null);
    setPreflight(null);
    setCatalog(null);
    setCoverage(null);
    setConversions(null);
    setTriage(null);
    setSelected([]);
    setStaffChecked(false);
    setMappingDraft(null);
    setMappingReason("");
    reviewKey.current=null;
  },[businessDate]);

  async function readCurrent(){
    const [next,proof,safety,mapping,verification,units,risk]=await Promise.all([
      catinatShiftApi.readiness(businessDate),
      catinatShiftApi.confirmations(businessDate),
      catinatShiftApi.closingPreflight(businessDate),
      catinatShiftApi.catalogMappingPreview(businessDate),
      catinatShiftApi.certificationCoverage(businessDate),
      catinatShiftApi.packageConversionReview(businessDate),
      catinatShiftApi.safetyTriage(businessDate),
    ]);
    if(next.source_hash!==proof.source_hash || next.source_hash!==safety.source_hash
       || next.source_hash!==mapping.source_hash || next.source_hash!==verification.source_hash
       || next.source_hash!==units.source_hash || next.source_hash!==risk.source_hash){
      setData(null);
      setConfirmed(null);
      setPreflight(null);
      setCatalog(null);
    setCoverage(null);
    setConversions(null);
    setTriage(null);
      throw new Error("Google Sheets thay đổi trong lúc kiểm tra. Hãy tải lại dữ liệu.");
    }
    setData(next);
    setConfirmed(proof);
    setPreflight(safety);
    setCatalog(mapping);
    setCoverage(verification);
    setConversions(units);
    setTriage(risk);
  }

  async function load(){
    if(busy||!businessDate)return;
    setBusy(true);
    setMessage("");
    setData(null);
    setConfirmed(null);
    setPreflight(null);
    setCatalog(null);
    setCoverage(null);
    setConversions(null);
    setTriage(null);
    setSelected([]);
    setStaffChecked(false);
    setMappingDraft(null);
    setMappingReason("");
    reviewKey.current=null;
    try{await readCurrent();}
    catch(error){setMessage(error instanceof Error?error.message:"Không thể đọc Google Sheets.");}
    finally{setBusy(false);}
  }

  const confirmedKeys=new Set(
    confirmed?.cells.filter(item=>item.confirmed).map(item=>itemKey(item.sheet,item.cell))||[],
  );
  const groups=data?summarizeCatinatReadiness(data.items):{};
  const safetyReasons=new Map<string,number>();
  for(const blocker of preflight?.blockers||[]) {
    safetyReasons.set(blocker.code,(safetyReasons.get(blocker.code)||0)+1);
  }
  const usable=Boolean(data&&confirmed&&data.source_hash===confirmed.source_hash);
  const selectable=selected.length>0&&selected.length<=50&&staffChecked&&usable&&!busy;

  function toggle(sheet:string,cell:string){
    const key=itemKey(sheet,cell);
    setSelected(previous=>previous.includes(key)
      ?previous.filter(item=>item!==key)
      :previous.length<50?[...previous,key]:previous);
    setStaffChecked(false);
  }

  async function confirmPhysicalCounts(){
    if(!selectable||!data)return;
    if(!window.confirm(
      "Tôi xác nhận đã kiểm đếm thực tế từng ô được chọn, số lượng đúng như Google Sheets. "+
      "Việc này chỉ ghi nhận xác nhận kiểm đếm, không đóng ca hoặc chuyển tồn."
    ))return;
    const cells=data.items.filter(item=>selected.includes(itemKey(item.sheet,item.cell)))
      .map(item=>({sheet:item.sheet,cell:item.cell}));
    setBusy(true);
    setMessage("");
    try{
      await catinatShiftApi.confirmCounts({
        business_date:businessDate,expected_source_hash:data.source_hash,cells,
      });
      setSelected([]);
      setStaffChecked(false);
      await readCurrent();
      setMessage("Đã lưu xác nhận kiểm đếm và người xác nhận. Ca tối CHƯA được chốt.");
    }catch(error){
      setData(null);
      setConfirmed(null);
      setPreflight(null);
      setCatalog(null);
    setCoverage(null);
    setConversions(null);
    setTriage(null);
      setSelected([]);
      setStaffChecked(false);
      setMessage(
        (error instanceof Error?error.message:"Không lưu được xác nhận.")+
        " Vui lòng tải lại và đối chiếu Google Sheets trước khi thử lại."
      );
    }finally{setBusy(false);}
  }

  async function requestCatalogMappingReview(){
    if(!mappingDraft||!catalog||busy)return;
    if(mappingReason.trim().length<8){
      setMessage("Vui lòng nhập lý do xác minh ánh xạ, ít nhất 8 ký tự.");
      return;
    }
    if(!window.confirm(
      "Gửi bằng chứng ô Sheets này để quản lý xét duyệt? "+
      "Thao tác chỉ tạo yêu cầu xét duyệt, không áp dụng mapping, chỉnh tồn hoặc ghi Sheets."
    ))return;
    setBusy(true);
    setMessage("");
    try{
      // Reload authoritative source and catalog before asking for approval.
      // Matching the workbook fingerprint alone does not detect catalog changes.
      const fresh=await catinatShiftApi.catalogMappingPreview(businessDate);
      const sourceRow=fresh.items.find(item=>
        item.sheet===mappingDraft.sheet&&item.cell===mappingDraft.cell);
      const currentLocation=fresh.locations.find(item=>item.sheet===mappingDraft.sheet);
      const oldLocation=catalog.locations.find(item=>item.sheet===mappingDraft.sheet);
      if(fresh.source_hash!==catalog.source_hash ||
         JSON.stringify(sourceRow)!==JSON.stringify(mappingDraft) ||
         JSON.stringify(currentLocation)!==JSON.stringify(oldLocation)){
        setCatalog(null);
    setCoverage(null);
    setConversions(null);
    setTriage(null);
        setMappingDraft(null);
        reviewKey.current=null;
        throw new Error("Dữ liệu Sheets/danh mục đã thay đổi. Tải lại trước khi tạo yêu cầu.");
      }
      if(!reviewKey.current)reviewKey.current=crypto.randomUUID();
      const proposal=buildCatinatMappingReviewProposal(fresh,mappingDraft);
      await catinatApprovalApi.create({
        idempotency_key:reviewKey.current,business_date:businessDate,
        kind:"catalog_mapping",
        subject_ref:itemKey(mappingDraft.sheet,mappingDraft.cell),
        proposal,reason:mappingReason.trim(),
      });
      setMappingDraft(null);
      setMappingReason("");
      reviewKey.current=null;
      setMessage("Đã gửi quản lý yêu cầu xác minh. Danh mục và tồn kho chưa được thay đổi.");
    }catch(error){
      if(error instanceof InventoryApiError&&error.status===403){
        setMessage("Tài khoản cần quyền đề xuất kiểm kê (inventory.review hoặc inventory.control).");
      }else{
        setMessage(error instanceof Error?error.message:"Chưa gửi được yêu cầu xác minh.");
      }
    }finally{setBusy(false);}
  }

  return <section className="inventory-daily">
    <h2>Kiểm kê ca tối · Đối chiếu trước khi chốt</h2>
    <p className="inventory-muted">
      Đọc dữ liệu từ ba kho và báo cáo sử dụng nguyên liệu.
      Nhân viên có thể xác nhận riêng từng ô sau khi kiểm đếm thực tế.
      <strong> Xác nhận ô không chốt ca hoặc hoàn tất đóng ca.</strong> Không tự ghi Google Sheets.
    </p>
    <div className="inventory-actions" style={{alignItems:"flex-end",flexWrap:"wrap"}}>
      <label htmlFor="catinat-shift-day">Ngày kinh doanh
        <input id="catinat-shift-day" type="date" value={businessDate}
          onChange={event=>setBusinessDate(event.target.value)}/>
      </label>
      <button type="button" disabled={busy||!businessDate} onClick={()=>void load()}>
        {busy?"Đang xử lý…":"Kiểm tra số liệu Google Sheets"}
      </button>
    </div>
    {message&&<p role="status" className="inventory-muted">{message}</p>}
    {data&&confirmed&&<>
      <p role="status" className="inventory-muted">
        Đã xác nhận {confirmed.confirmed_count}/{confirmed.required_count} ô tồn.
        Ca vẫn chưa được chốt; cần xử lý thêm {data.blockers.length} mục kiểm tra/cảnh báo.
      </p>
      <p className="inventory-muted">
        Nguồn: {data.source_hash.slice(0,12)}… · {data.items.length} ô tồn cần rà soát.
        Bất kỳ thay đổi nào trên Sheets đều có thể làm xác nhận cũ không còn hợp lệ.
      </p>
      <div className="inventory-actions" style={{flexWrap:"wrap"}}>
        {Object.entries(groups).map(([sheet,group])=>
          <section key={sheet} className="inventory-material-candidate">
            <strong>{sheet}</strong>
            <p>{group.present} có số, {group.missing} thiếu/mơ hồ, tổng {group.total}</p>
          </section>
        )}
      </div>
      <h3>Ô tồn cuối theo kho</h3>
      <p className="inventory-muted">Chỉ chọn những ô bạn vừa tự kiểm đếm, tối đa 50 ô mỗi lần.</p>
      <div style={{overflowX:"auto"}}>
        <table>
          <thead><tr><th>Xác nhận</th><th>Kho</th><th>Nguyên liệu</th><th>Phần tồn</th><th>Ô Sheets</th><th>Giá trị</th><th>Trạng thái</th></tr></thead>
          <tbody>
            {data.items.map(item=>{
              const key=itemKey(item.sheet,item.cell);
              const saved=confirmedKeys.has(key);
              const valid=item.quantity!==null&&Boolean(item.unit);
              return <tr key={key}>
                <td><input type="checkbox" aria-label={`Xác nhận đã kiểm đếm ${item.material} ${item.sheet} ${item.cell}`}
                  disabled={!valid||saved||busy}
                  checked={saved||selected.includes(key)}
                  onChange={()=>toggle(item.sheet,item.cell)}/></td>
                <td>{item.sheet}</td><td>{item.material}</td>
                <td>{item.form==="whole"?"Nguyên":"Lẻ"}</td>
                <td>{item.cell}</td>
                <td>{item.raw||"Trống"} {item.unit}</td>
                <td>{saved?"Đã xác nhận":(ISSUE[item.blocker]||item.blocker)}</td>
              </tr>;
            })}
          </tbody>
        </table>
      </div>
      {selected.length>0&&<section style={{marginTop:16}}>
        <label htmlFor="catinat-confirm-physical">
          <input id="catinat-confirm-physical" type="checkbox" checked={staffChecked}
            disabled={busy} onChange={event=>setStaffChecked(event.target.checked)}/>
          Tôi đã trực tiếp kiểm đếm thực tế {selected.length} ô được chọn và số lượng khớp với Google Sheets.
        </label>
        <div className="inventory-actions">
          <button type="button" disabled={!selectable}
            onClick={()=>void confirmPhysicalCounts()}>
            {busy?"Đang lưu…":`Lưu xác nhận kiểm đếm (${selected.length} ô)`}
          </button>
        </div>
      </section>}
      {preflight&&<section>
        <h3>Điều kiện bắt buộc trước khi hoàn tất ca tối</h3>
        <p className="inventory-muted">
          Đã xác nhận {preflight.confirmed_count}/{preflight.required_count} ô tồn trên cùng phiên bản Sheets.
          Còn {preflight.outstanding_transfer_count} chuyến chưa nhận đủ,
          {preflight.unreconciled_transfer_count} chuyển kho chưa đối chiếu sổ tồn và
          {preflight.approval_requests_count} yêu cầu quản lý liên quan.
          <strong> Chưa cho phép chốt ca hoặc tự chuyển tồn.</strong>
        </p>
        <p className="inventory-muted">
          Danh mục hiện có {preflight.catalog_coverage.active_item_count} nguyên liệu và
          {" "}{preflight.catalog_coverage.active_location_count} kho/vị trí đang hoạt động.
          <strong> Chưa có xác nhận ghép từng dòng Sheets với danh mục chuẩn.</strong>
        </p>
        <ul>{[...safetyReasons.entries()].map(([code,count])=>
          <li key={code}>{ISSUE[code]||code}: {count} mục</li>)}</ul>
      </section>}
      {triage&&<section aria-label="Theo dõi hạn sử dụng hủy hàng và chuyển kho">
        <h3>Hạn sử dụng, hủy nguyên liệu và chuyển kho</h3>
        <p className="inventory-muted">
          Có {triage.summary.physical_cells_without_batch_expiry_certification} ô tồn chưa đủ
          chứng cứ lô/hạn dùng, {triage.summary.date_only_preparation_hints} dòng chế biến
          chỉ có ngày mà thiếu thời điểm chính xác. Còn
          {" "}{triage.summary.pending_receipt_transfers} chuyến chưa nhận đủ và
          {" "}{triage.summary.unposted_ledger_transfers} chuyến chưa được ghi sổ.
          <strong> Không tự xác nhận hạn dùng, hủy nguyên liệu hoặc điều chỉnh tồn.</strong>
        </p>
        <details>
          <summary>Dòng chế biến cần xác minh giờ và hạn dùng ({triage.prepared_report_hints.length})</summary>
          {triage.prepared_report_hints.length?
            <ul>{triage.prepared_report_hints.map((row,index)=>
              <li key={row.sheet+":"+row.row+":"+index}>{row.sheet} · dòng {row.row} —
                nhân viên cần xác minh chính xác giờ chế biến, lô và hạn dùng (tối đa 96 giờ,
                trừ khi có quy định ngắn hơn).</li>)}</ul>:
            <p>Chưa thấy dấu hiệu ngày chế biến theo mẫu trong dữ liệu đang đọc.
              Điều này không chứng nhận các lô đã an toàn.</p>}
        </details>
        <details>
          <summary>Ô tồn chưa có chứng cứ lô/hủy hàng ({triage.items.length})</summary>
          <div style={{overflowX:"auto"}}>
            <table><thead><tr><th>Kho</th><th>Ô</th><th>Nguyên liệu</th><th>Yêu cầu</th></tr></thead>
              <tbody>{triage.items.map(row=><tr key={itemKey(row.sheet,row.cell)}>
                <td>{row.sheet}</td><td>{row.cell}</td><td>{row.material}</td>
                <td>Cần mã lô, thời điểm mở/chế biến, quy định hạn dùng và bằng chứng
                    hủy (nếu có); chưa xác nhận</td>
              </tr>)}</tbody>
            </table>
          </div>
        </details>
        <details>
          <summary>Chuyển kho cần đối soát ({triage.transfers.length})</summary>
          {triage.transfers.length?
            <div style={{overflowX:"auto"}}><table>
              <thead><tr><th>Chuyển kho</th><th>Nguyên liệu</th><th>Đã gửi</th><th>Đã nhận</th>
                <th>Đang chuyển</th><th>Trạng thái</th></tr></thead>
              <tbody>{triage.transfers.map(t=><tr key={t.id}>
                <td>{t.id}</td><td>{t.item_id}</td>
                <td>{t.dispatched_quantity} {t.unit}</td><td>{t.received_quantity} {t.unit}</td>
                <td>{t.remaining_in_transit} {t.unit}</td>
                <td>{t.requires_receipt?"Cần xác nhận nhận đủ. ":""}
                  {t.requires_ledger_reconciliation?"Chưa đối soát sổ tồn. ":""}
                  {!t.requires_receipt&&!t.requires_ledger_reconciliation?
                    "Có trạng thái hoàn tất trong bảng theo dõi, chưa xác nhận chốt ca":""}</td>
              </tr>)}</tbody>
            </table></div>:
            <p>Chưa có sự kiện chuyển kho trong bảng theo dõi tại ngày kiểm tra.
              Không đồng nghĩa sổ kho đã được đối soát.</p>}
        </details>
      </section>}
      {conversions&&<section aria-label="Kiểm tra quy đổi nguyên liệu Catinat">
        <h3>Kiểm tra quy đổi bao bì và đơn vị nguyên liệu</h3>
        <p className="inventory-muted">
          Tìm thấy chứng cứ quy đổi có người duyệt và khớp danh mục ở
          {" "}{conversions.summary.approved_conversion_evidence_cells}/
          {conversions.summary.total_cells} ô đã đọc. Các ô tồn lẻ hoặc không cần quy đổi
          được phân loại riêng; không tính là quy đổi đã được duyệt.
          <strong> Chưa quy đổi số tồn, chưa ghi sổ hoặc chốt ca.</strong>
        </p>
        <details>
          <summary>Xem trạng thái quy đổi theo từng ô ({conversions.items.length})</summary>
          <div style={{overflowX:"auto"}}>
            <table>
              <thead><tr><th>Kho</th><th>Ô</th><th>Nguyên liệu</th><th>Đơn vị trên Sheets</th><th>Bằng chứng và việc cần làm</th></tr></thead>
              <tbody>{conversions.items.map(row=><tr key={itemKey(row.sheet,row.cell)}>
                <td>{row.sheet}</td><td>{row.cell}</td><td>{row.material}</td>
                <td>{row.form==="whole"?"Nguyên":"Lẻ"} · {row.source_unit}</td>
                <td>{{
                  catalog_mapping_requires_review:"Đối chiếu nguyên liệu và đơn vị trong danh mục trước",
                  whole_unit_mismatch:"Đơn vị bao bì không khớp danh mục",
                  loose_unit_mismatch:"Đơn vị tồn lẻ không khớp danh mục",
                  loose_unit_matches_catalog:"Đơn vị tồn lẻ khớp, không chứng nhận quy đổi",
                  conversion_not_needed_same_unit:"Đơn vị nguyên và lẻ giống nhau, không cần quy đổi",
                  approved_package_conversion_missing:"Chưa có hệ số quy đổi được xác nhận riêng",
                  package_conversion_not_approved:"Có hệ số nhưng chưa có người phê duyệt",
                  approved_conversion_unit_or_factor_invalid:"Đơn vị đích hoặc hệ số không hợp lệ",
                  approved_conversion_disagrees_with_catalog:"Hệ số đã duyệt khác dữ liệu danh mục",
                  approved_conversion_evidence_found:"Có chứng cứ duyệt — chưa áp dụng vào tồn",
                }[row.status]||row.status}
                {row.approved_factor_evidence&&row.approved_target_unit_evidence?
                  " · 1 bao bì = "+row.approved_factor_evidence+" "+row.approved_target_unit_evidence:""}</td>
              </tr>)}</tbody>
            </table>
          </div>
        </details>
      </section>}
      {coverage&&<section aria-label="Tiến độ xác minh ba khu vực Catinat">
        <h3>Tiến độ xác minh danh mục ba khu vực</h3>
        <p className="inventory-muted">
          Chứng cứ quản lý phù hợp nguồn Sheets hiện tại: {coverage.summary.evidence_cells}/
          {coverage.summary.source_cells} ô.
          Tên kho khớp duy nhất: {coverage.summary.warehouse_areas_with_exact_candidate}/
          {coverage.summary.total_warehouse_areas} khu vực.
          <strong> Chứng cứ không kích hoạt ánh xạ, không chốt ca hoặc ghi Google Sheets.</strong>
        </p>
        <div style={{overflowX:"auto"}}>
          <table>
            <thead><tr><th>Khu vực</th><th>Ô trên Sheets</th><th>Đã có chứng cứ</th><th>Chờ xác minh</th><th>Tình trạng kho</th></tr></thead>
            <tbody>{coverage.areas.map(area=><tr key={area.sheet}>
              <td>{area.sheet}</td><td>{area.source_cells}</td>
              <td>{area.evidence_cells}</td><td>{area.pending_cells}</td>
              <td>{area.missing_location?"Chưa có kho trong danh mục":
                area.ambiguous_location?"Nhiều kho trùng tên — cần xử lý":
                "Kho khớp tên, cần quản lý xác minh"}</td>
            </tr>)}</tbody>
          </table>
        </div>
        <p>
          Nếu thiếu kho hoặc sai nguyên liệu/đơn vị, quản lý đối chiếu trực tiếp trong{" "}
          <a href="/inventory/materials">Nguyên vật liệu → Danh mục kho</a>.
          Chỉ tạo hoặc sửa dữ liệu chuẩn khi đã đối chiếu thực tế.
        </p>
      </section>}
      {catalog&&<section>
        <h3>Đối chiếu danh mục nguyên liệu và vị trí kho</h3>
        <p className="inventory-muted">
          Tìm thấy {catalog.summary.exact_candidates}/{catalog.summary.total} ô có tên nguyên liệu,
          đơn vị và tên kho khớp chính xác; còn {catalog.summary.requires_review} ô cần rà soát.
          <strong> Đây chỉ là ứng viên, chưa được quản lý duyệt và không được dùng để chốt tồn.</strong>
        </p>
        <div style={{overflowX:"auto"}}>
          <table>
            <thead><tr><th>Khu vực trên Sheets</th><th>Trạng thái danh mục kho</th></tr></thead>
            <tbody>{catalog.locations.map(place=><tr key={place.sheet}>
              <td>{place.sheet}</td>
              <td>{CATINAT_MAPPING_STATUS[place.status]||place.status}</td>
            </tr>)}</tbody>
          </table>
        </div>
        <details>
          <summary>Xem đối chiếu theo từng ô nguyên liệu ({catalog.items.length})</summary>
          <div style={{overflowX:"auto"}}>
            <table>
              <thead><tr><th>Kho</th><th>Ô Sheets</th><th>Nguyên liệu</th><th>Đơn vị</th><th>Kết quả đối chiếu</th><th>Yêu cầu</th></tr></thead>
              <tbody>{catalog.items.map(row=><tr key={itemKey(row.sheet,row.cell)}>
                <td>{row.sheet}</td><td>{row.cell}</td><td>{row.material}</td><td>{row.unit||"Thiếu"}</td>
                <td>{CATINAT_MAPPING_STATUS[row.status]||row.status}</td>
                <td><button type="button" disabled={busy} onClick={()=>{
                  setMappingDraft(row);setMappingReason("");reviewKey.current=null;
                }}>Đề nghị xác minh</button></td>
              </tr>)}</tbody>
            </table>
          </div>
        </details>
        {mappingDraft&&<div className="inventory-material-candidate" style={{marginTop:12}}>
          <h4>Gửi quản lý xác minh · {mappingDraft.sheet} · {mappingDraft.cell}</h4>
          <p className="inventory-muted">
            {mappingDraft.material} ({mappingDraft.unit||"chưa có đơn vị"}).
            Yêu cầu đính kèm phiên bản Sheets và các ứng viên hiện có.
            <strong> Việc xét duyệt không tự sửa danh mục, tồn kho hay Google Sheets.</strong>
          </p>
          <label htmlFor="catinat-mapping-reason">Lý do cần quản lý xác minh (ít nhất 8 ký tự)</label>
          <textarea id="catinat-mapping-reason" rows={2} value={mappingReason}
            onChange={event=>{setMappingReason(event.target.value);reviewKey.current=null;}}
            placeholder="Nêu nguyên liệu, kho hoặc đơn vị cần xác minh"/>
          <div className="inventory-actions">
            <button type="button" disabled={busy||mappingReason.trim().length<8}
              onClick={()=>void requestCatalogMappingReview()}>
              {busy?"Đang gửi…":"Gửi yêu cầu xác minh"}
            </button>
            <button type="button" disabled={busy} onClick={()=>{
              setMappingDraft(null);setMappingReason("");reviewKey.current=null;
            }}>Hủy</button>
          </div>
        </div>}
      </section>}
      <h3>Cảnh báo cần xử lý trước khi đóng ca</h3>
      <ul>{data.blockers.filter(row=>row.code!=="employee_physical_confirmation_required"
        &&row.code!=="physical_count_missing_or_ambiguous").map((row,index)=>
          <li key={index}>{row.sheet||"Toàn bộ"} {row.row?("dòng "+row.row):""}: {ISSUE[row.code]||row.code}</li>
        )}
      </ul>
      <p className="inventory-muted">
        <strong>Không tự điền ô trống.</strong> Nhân viên phải bổ sung và xác nhận mọi tồn thực tế.
        Ngoại lệ vẫn cần quản lý duyệt riêng; chức năng chốt ca chính thức chưa được bật.
      </p>
    </>}
  </section>;
}
