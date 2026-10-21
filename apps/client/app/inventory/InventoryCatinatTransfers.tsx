import {useEffect,useRef,useState} from "react";
import {
  catinatTransferApi, inventoryMaterialApi,
  type CatinatTransferRecord, type InventoryMaterial,
  type InventoryRegistryReadiness,
} from "./api";

function todayVietnam() {
  const parts=new Intl.DateTimeFormat("en-CA",{
    timeZone:"Asia/Ho_Chi_Minh",year:"numeric",month:"2-digit",day:"2-digit",
  }).formatToParts(new Date());
  const find=(part:string)=>parts.find(value=>value.type===part)?.value||"";
  return [find("year"),find("month"),find("day")].join("-");
}

export function InventoryCatinatTransfers(){
  const [materials,setMaterials]=useState<InventoryMaterial[]>([]);
  const [catalog,setCatalog]=useState<InventoryRegistryReadiness|null>(null);
  const [transfers,setTransfers]=useState<CatinatTransferRecord[]>([]);
  const [pendingOnly,setPendingOnly]=useState(true);
  const [date,setDate]=useState(todayVietnam);
  const [itemId,setItemId]=useState("");
  const [sourceId,setSourceId]=useState("");
  const [destinationId,setDestinationId]=useState("");
  const [unit,setUnit]=useState("");
  const [quantity,setQuantity]=useState("");
  const [receivingId,setReceivingId]=useState("");
  const [receivingQuantity,setReceivingQuantity]=useState("");
  const [checked,setChecked]=useState(false);
  const [message,setMessage]=useState("");
  const [busy,setBusy]=useState(false);
  const dispatchKey=useRef<string|null>(null);
  const receiptKey=useRef<string|null>(null);

  async function reload(showConfiguration=false){
    const [listing,registry,materialsList]=await Promise.all([
      catinatTransferApi.list(pendingOnly),
      showConfiguration?inventoryMaterialApi.readiness():Promise.resolve(null),
      showConfiguration?inventoryMaterialApi.list():Promise.resolve(null),
    ]);
    setTransfers(listing.items);
    if(registry)setCatalog(registry);
    if(materialsList)setMaterials(materialsList.items.filter(item=>item.active));
  }
  useEffect(()=>{
    let active=true;
    Promise.all([
      catinatTransferApi.list(pendingOnly),
      inventoryMaterialApi.readiness(),inventoryMaterialApi.list(),
    ]).then(([listing,registry,materialList])=>{
      if(!active)return;
      setTransfers(listing.items);setCatalog(registry);
      setMaterials(materialList.items.filter(item=>item.active));
    }).catch(error=>{if(active)setMessage(error instanceof Error?error.message:"Không thể tải chuyển kho.");});
    return()=>{active=false;};
  },[pendingOnly]);

  const warehouses=catalog?.warehouses.filter(w=>w.active)||[];
  const chosen=materials.find(item=>item.material_id===itemId);

  function changeDispatch(action:()=>void){action();dispatchKey.current=null;}
  function changeReceipt(action:()=>void){action();receiptKey.current=null;setChecked(false);}

  async function dispatch(){
    if(busy)return;
    if(!itemId||!sourceId||!destinationId||sourceId===destinationId||!unit.trim()||!quantity.trim()){
      setMessage("Chọn nguyên liệu, hai kho khác nhau, đơn vị và số lượng bàn giao.");
      return;
    }
    if(!window.confirm(
      "Xác nhận đã bàn giao số lượng thực tế? Đây là sổ theo dõi bàn giao, "+
      "chưa tự trừ/cộng tồn chính hoặc ghi Google Sheets."
    ))return;
    setBusy(true);setMessage("");
    try{
      if(!dispatchKey.current)dispatchKey.current=crypto.randomUUID();
      const transfer=await catinatTransferApi.dispatch({
        reference_key:dispatchKey.current,business_date:date,item_id:itemId,
        source_location_id:sourceId,destination_location_id:destinationId,
        unit:unit.trim(),quantity:quantity.trim(),
      });
      setMessage("Đã lưu bàn giao "+transfer.id+". Chờ bên nhận xác nhận. Chưa cập nhật sổ tồn chính.");
      dispatchKey.current=null;setQuantity("");await reload();
    }catch(error){
      setMessage((error instanceof Error?error.message:"Bàn giao thất bại.")+
        " Nếu mất mạng, thử lại với biểu mẫu giữ nguyên để tránh giao dịch trùng.");
    }finally{setBusy(false);}
  }

  async function receive(){
    const row=transfers.find(item=>item.id===receivingId);
    if(!row||!receivingQuantity.trim()||!checked||busy)return;
    if(!window.confirm(
      "Bạn xác nhận đã thực sự nhận "+receivingQuantity+" "+row.unit+
      " tại kho đến? Chưa tự cộng tồn chính hoặc ghi Google Sheets."
    ))return;
    setBusy(true);setMessage("");
    try{
      if(!receiptKey.current)receiptKey.current=crypto.randomUUID();
      const result=await catinatTransferApi.receive(row.id,{
        event_key:receiptKey.current,quantity:receivingQuantity.trim(),
      });
      setMessage("Đã lưu số thực nhận. Còn đang chuyển: "+result.in_transit_quantity+" "+result.unit+
        ". Chờ đối chiếu với sổ tồn chính.");
      receiptKey.current=null;setReceivingId("");setReceivingQuantity("");
      setChecked(false);await reload();
    }catch(error){
      setMessage((error instanceof Error?error.message:"Xác nhận nhận hàng thất bại.")+
        " Nếu chưa rõ đã lưu, không thay đổi số lượng trước khi thử lại.");
    }finally{setBusy(false);}
  }

  return <section className="inventory-daily">
    <h2>Chuyển kho · Theo dõi hàng đang chuyển</h2>
    <p className="inventory-muted">
      Người bàn giao xác nhận xuất và bên nhận xác nhận lượng thực nhận riêng.
      Chênh lệch/chuyển kho còn chờ phải xử lý trước khi đóng ca hoặc được quản lý duyệt ngoại lệ.
      <strong> Sổ này chưa tự ghi Google Sheets, chưa tự trừ/cộng tồn chính.</strong>
    </p>
    {message&&<p role="status" className="inventory-muted">{message}</p>}
    <label htmlFor="catinat-transfers-pending">
      <input id="catinat-transfers-pending" type="checkbox" checked={pendingOnly}
        onChange={event=>setPendingOnly(event.target.checked)}/>
      Chỉ hiện chuyển kho chưa nhận đủ
    </label>
    <button type="button" disabled={busy} onClick={()=>void reload().catch(error=>
      setMessage(error instanceof Error?error.message:"Không thể tải lại.")
    )}>Làm mới</button>
    <h3>Danh sách chuyển kho</h3>
    <div style={{overflowX:"auto"}}>
      <table><thead><tr>
        <th>Ngày</th><th>Nguyên liệu</th><th>Kho đi → kho đến</th>
        <th>Bàn giao</th><th>Đã nhận</th><th>Đang chuyển</th><th>Trạng thái</th>
      </tr></thead><tbody>{transfers.map(row=><tr key={row.id}>
        <td>{row.business_date}</td>
        <td>{materials.find(m=>m.material_id===row.item_id)?.canonical_name||row.item_id}</td>
        <td>{warehouses.find(w=>w.id===row.source_location_id)?.name||row.source_location_id}
          {" → "}{warehouses.find(w=>w.id===row.destination_location_id)?.name||row.destination_location_id}</td>
        <td>{row.dispatched_quantity} {row.unit}</td>
        <td>{row.received_quantity} {row.unit}</td>
        <td>{row.in_transit_quantity} {row.unit}</td>
        <td>{row.status==="received"?"Đã nhận đủ":row.status==="partial_receipt"?"Nhận một phần":"Chờ nhận"}
          {row.status!=="received"&&<button type="button" disabled={busy} onClick={()=>
            changeReceipt(()=>{setReceivingId(row.id);setReceivingQuantity(row.in_transit_quantity);})
          }>Xác nhận nhận</button>}
        </td>
      </tr>)}</tbody></table>
    </div>
    {transfers.length===0&&<p className="inventory-muted">Chưa có giao dịch thuộc bộ lọc.</p>}
    {receivingId&&<section>
      <h3>Xác nhận hàng thực nhận</h3>
      <label htmlFor="catinat-received-quantity">Số lượng đã nhận thực tế
        <input id="catinat-received-quantity" value={receivingQuantity}
          onChange={event=>changeReceipt(()=>setReceivingQuantity(event.target.value))}/>
      </label>
      <label htmlFor="catinat-received-attest">
        <input id="catinat-received-attest" type="checkbox" checked={checked}
          onChange={event=>setChecked(event.target.checked)}/>
        Tôi đã nhận và kiểm đếm số lượng trên tại kho đến.
      </label>
      <button type="button" disabled={busy||!checked||!receivingQuantity.trim()}
        onClick={()=>void receive()}>Lưu xác nhận bên nhận</button>
    </section>}
    <h3>Tạo bàn giao mới</h3>
    {(!warehouses.length||!materials.length)&&
      <p className="inventory-muted">Catalog hoặc kho chưa được phê duyệt đầy đủ; không thể tạo bàn giao hợp lệ.</p>}
    <div className="inventory-actions" style={{flexWrap:"wrap"}}>
      <label htmlFor="catinat-transfer-date">Ngày kinh doanh
        <input id="catinat-transfer-date" type="date" value={date}
          onChange={event=>changeDispatch(()=>setDate(event.target.value))}/>
      </label>
      <label htmlFor="catinat-transfer-material">Nguyên liệu đã duyệt
        <select id="catinat-transfer-material" value={itemId} onChange={event=>
          changeDispatch(()=>{setItemId(event.target.value);
            setUnit(materials.find(m=>m.material_id===event.target.value)?.preferred_unit||"");})
        }>
          <option value="">Chọn nguyên liệu</option>
          {materials.map(item=><option key={item.material_id} value={item.material_id}>
            {item.canonical_name}</option>)}
        </select>
      </label>
      <label htmlFor="catinat-transfer-from">Kho bàn giao
        <select id="catinat-transfer-from" value={sourceId}
          onChange={event=>changeDispatch(()=>setSourceId(event.target.value))}>
          <option value="">Chọn kho đi</option>
          {warehouses.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
      </label>
      <label htmlFor="catinat-transfer-to">Kho nhận
        <select id="catinat-transfer-to" value={destinationId}
          onChange={event=>changeDispatch(()=>setDestinationId(event.target.value))}>
          <option value="">Chọn kho đến</option>
          {warehouses.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
      </label>
      <label htmlFor="catinat-transfer-unit">Đơn vị
        <input id="catinat-transfer-unit" value={unit}
          placeholder={chosen?.preferred_unit||"lon / gói / gr"}
          onChange={event=>changeDispatch(()=>setUnit(event.target.value))}/>
      </label>
      <label htmlFor="catinat-transfer-quantity">Số lượng
        <input id="catinat-transfer-quantity" value={quantity} inputMode="decimal"
          onChange={event=>changeDispatch(()=>setQuantity(event.target.value))}/>
      </label>
    </div>
    <button type="button" disabled={busy||!warehouses.length||!materials.length}
      onClick={()=>void dispatch()}>Lưu bàn giao, chờ bên nhận xác nhận</button>
    <p className="inventory-muted">
      Không sử dụng quy trình này để điều chỉnh số tồn hay tự xác nhận hàng đã tới nơi.
      Bất kỳ chênh lệch nào phải được đối chiếu với Google Sheets và quản lý.
    </p>
  </section>;
}
