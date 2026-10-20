import { useEffect, useState } from "react";
import {
  catinatShiftApi, type CatinatEveningReadiness, type CatinatPhysicalReadinessItem,
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
};

export function InventoryCatinatShiftReadiness(){
  const [businessDate,setBusinessDate]=useState(todayVietnam);
  const [data,setData]=useState<CatinatEveningReadiness|null>(null);
  const [busy,setBusy]=useState(false);
  const [message,setMessage]=useState("");
  useEffect(()=>{setData(null);},[businessDate]);
  async function load(){
    if(busy||!businessDate)return;
    setBusy(true);
    setMessage("");
    setData(null);
    try{
      setData(await catinatShiftApi.readiness(businessDate));
    }catch(error){
      setMessage(error instanceof Error?error.message:"Không thể đọc Google Sheets.");
    }finally{setBusy(false);}
  }
  const groups=data?summarizeCatinatReadiness(data.items):{};
  return <section className="inventory-daily">
    <h2>Kiểm kê ca tối · Đối chiếu trước khi chốt</h2>
    <p className="inventory-muted">
      Chỉ đọc dữ liệu từ ba kho và báo cáo sử dụng nguyên liệu. Màn hình này
      không xác nhận kiểm đếm, không chốt ca và không ghi Google Sheets.
    </p>
    <div className="inventory-actions" style={{alignItems:"flex-end",flexWrap:"wrap"}}>
      <label htmlFor="catinat-shift-day">Ngày kinh doanh
        <input id="catinat-shift-day" type="date" value={businessDate}
          onChange={event=>setBusinessDate(event.target.value)}/>
      </label>
      <button type="button" disabled={busy||!businessDate} onClick={()=>void load()}>
        {busy?"Đang đọc…":"Kiểm tra số liệu Google Sheets"}
      </button>
    </div>
    {message&&<p role="alert" className="inventory-error">{message}</p>}
    {data&&<>
      <p role="status" className="inventory-error">
        Chưa đủ điều kiện chốt ca — {data.blockers.length} mục cần kiểm tra hoặc xác nhận.
      </p>
      <p className="inventory-muted">
        Nguồn: {data.source_hash.slice(0,12)}… · {data.items.length} ô tồn cần rà soát.
        Dữ liệu có thể thay đổi khi nhân viên tiếp tục sửa Google Sheets.
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
      <div style={{overflowX:"auto"}}>
        <table>
          <thead><tr><th>Kho</th><th>Nguyên liệu</th><th>Phần tồn</th><th>Ô Sheets</th><th>Giá trị</th><th>Trạng thái</th></tr></thead>
          <tbody>
            {data.items.map(item=><tr key={item.sheet+item.cell}>
              <td>{item.sheet}</td><td>{item.material}</td>
              <td>{item.form==="whole"?"Nguyên":"Lẻ"}</td>
              <td>{item.cell}</td>
              <td>{item.raw||"Trống"} {item.unit}</td>
              <td>{ISSUE[item.blocker]||item.blocker}</td>
            </tr>)}
          </tbody>
        </table>
      </div>
      <h3>Cảnh báo khác</h3>
      <ul>{data.blockers.filter(row=>row.code!=="employee_physical_confirmation_required"
        &&row.code!=="physical_count_missing_or_ambiguous").map((row,index)=>
          <li key={index}>{row.sheet||"Toàn bộ"} {row.row?("dòng "+row.row):""}: {ISSUE[row.code]||row.code}</li>
        )}
      </ul>
      <p className="inventory-muted">
        <strong>Không tự điền ô trống.</strong> Nhân viên cần kiểm đếm và xác nhận từng giá trị.
        Mọi ngoại lệ phải được quản lý duyệt riêng; chưa bật chức năng chốt chính thức.
      </p>
    </>}
  </section>;
}
