// @vitest-environment jsdom
import {it,expect,vi} from "vitest";
import {parseReceiptBundle,importSmsReceipts,parseStage,SmsSyncReceipts} from "./SmsSyncReceipts";
import {act} from "react";
import {createRoot} from "react-dom/client";
(globalThis as typeof globalThis & {IS_REACT_ACT_ENVIRONMENT:boolean}).IS_REACT_ACT_ENVIRONMENT=true;
const shop="Tiktok_McGowanshopdesigns";
const receipt={confirm:true,candidate_id:"a".repeat(64),idea_code:"I42",idea_id:"11111111-1111-1111-1111-111111111111",target_id:"22222222-2222-2222-2222-222222222222",shop,reference_images:2,target_videos:1,listings_made:0,listings_live:0,etsy_id:"1234567890",vertical_field_url:"https://drive.google.com/file/d/exampleVertical123/view"};
const bundle=(rows:unknown[])=>({schema:"cam-sms-sync-v3-operator-receipts-v1",shop,receipts:rows});
const ack=(r:typeof receipt)=>({state:"reported_counts_match",live_sms_verified_by_cam:false,target_video_identity_verified:false,record:{...r,reported_at:"2026-11-28T00:00:00Z"},changed:true});
it("validates exact receipt-only schema and unique identities",()=>{
 expect(parseReceiptBundle(bundle([receipt]))).toEqual([receipt]);
 for(const rows of [[],[receipt,receipt],[{...receipt,target_videos:0}],[{...receipt,shop:"other"}],[{...receipt,etsy_id:"../all"}],[{...receipt,vertical_field_url:"https://other.example/file"}],[{...receipt,targets:[]}],[{...receipt,listings_live:1}]]){
  expect(()=>parseReceiptBundle(bundle(rows))).toThrow();
 }
});
it("writes only existing authorized receipt endpoint and checks acknowledgment",async()=>{
 const fetcher=vi.fn(async()=>ack(receipt));
 const onAck=vi.fn();
 await importSmsReceipts(parseReceiptBundle(bundle([receipt])),onAck,fetcher as unknown as Parameters<typeof importSmsReceipts>[2]);
 expect(fetcher).toHaveBeenCalledWith("/listings/1234567890/sms-sync-readback",{method:"POST",body:JSON.stringify(receipt)});
 expect(onAck).toHaveBeenCalledTimes(1);
});
it("halts on conflicting readback and does not retry or continue",async()=>{
 const second={...receipt,etsy_id:"1234567891",idea_code:"I43",idea_id:"33333333-3333-3333-3333-333333333333",target_id:"44444444-4444-4444-4444-444444444444"};
 const fetcher=vi.fn(async()=>({...ack(receipt),record:{...receipt,idea_code:"I999",reported_at:"2026-11-28T00:00:00Z"}}));
 await expect(importSmsReceipts(parseReceiptBundle(bundle([receipt,second])),vi.fn(),fetcher as unknown as Parameters<typeof importSmsReceipts>[2])).rejects.toThrow("Dừng");
 expect(fetcher).toHaveBeenCalledTimes(1);
});
const prepared=(saved=false)=>({...bundle(saved?[]:[receipt]),state:"prepared",live_sms_verified_by_cam:false,observed_at:"2026-11-28T03:08:21Z",sms_observed_count:1,recorded_count:saved?1:0,pending_count:saved?0:1,review_count:0,held_count:2,quota:{state:"SMS_DAILY_LIMIT",limit:20}});
it("rejects inconsistent staged counts and allows zero pending rows",()=>{
 expect(parseStage(prepared(true)).receipts).toEqual([]);
 expect(()=>parseStage({...prepared(),pending_count:2})).toThrow();
 expect(()=>parseStage({...prepared(),live_sms_verified_by_cam:true})).toThrow();
});
it("loads staged evidence on demand with no file picker; posts only on the explicit action",async()=>{
 const mount=document.createElement("div");document.body.appendChild(mount);
 const root=createRoot(mount);
 let saved=false;
 const spy=vi.fn(async(_url:RequestInfo|URL,init?:RequestInit)=>{
  const post=init?.method==="POST";
  if(post)saved=true;
  return new Response(JSON.stringify(post?ack(receipt):prepared(saved)),{headers:{"content-type":"application/json"}});
 });
 vi.stubGlobal("fetch",spy);
 try{
  await act(async()=>root.render(<SmsSyncReceipts canManage={false}/>));
  expect(mount.querySelector("button")).toBeNull();
  await act(async()=>root.render(<SmsSyncReceipts canManage={true}/>));
  expect(spy).not.toHaveBeenCalled();
  await act(async()=>mount.querySelector("button")!.click());
  const panel=document.querySelector('[aria-label="SMS Sync"]')!;
  expect(panel).toBeTruthy();
  expect(panel.querySelector('input[type="file"]')).toBeNull();
  expect(panel.textContent).toContain("Chờ gửi");
  expect(panel.textContent).toContain("20/ngày");
  expect(spy).toHaveBeenCalledTimes(1);
  const submit=Array.from(panel.querySelectorAll("button")).find(b=>b.textContent?.startsWith("Ghi nhận 1 sản phẩm"))!;
  expect(submit.disabled).toBe(false);
  await act(async()=>submit.click());
  expect(spy).toHaveBeenCalledTimes(3);
  expect(String(spy.mock.calls[0][0])).toContain("/sms-sync-stage");
  expect(String(spy.mock.calls[1][0])).toContain("/sms-sync-readback");
  expect(spy.mock.calls[1][1]?.method).toBe("POST");
  expect(panel.textContent).toContain("CAM đã ghi nhận toàn bộ");
 }finally{await act(async()=>root.unmount());mount.remove();vi.unstubAllGlobals();}
});
