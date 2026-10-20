// @vitest-environment jsdom
import {act} from "react";
import {createRoot} from "react-dom/client";
import {afterEach,describe,expect,it,vi} from "vitest";
import {BrandingDnaBrowserReviewQueue} from "./BrandingDnaBrowserReviewQueue";

afterEach(()=>{vi.unstubAllGlobals();document.body.innerHTML="";});

describe("Branding DNA browser pilot independent review inbox",()=>{
 it("renders pending queue, reports without media, reviews as unverified with explicit user action",async()=>{
  const shop="11111111-1111-4111-8111-111111111111";
  const report="22222222-2222-4222-8222-222222222222";
  const base="/api/v1/branding-dna";
  const queue={items:[{id:report,shop_id:shop,platform:"etsy",
   report_key:"pilot:testing-immutable-key",observed_count:1,
   created_at:"2026-10-20T11:10:00Z",review_state:"pending_independent_review",
   source_verified:false,rights_verified:false,scan_enabled:false}],
   count:1,limit:20,offset:0,source_verified:false,rights_verified:false,
   scan_enabled:false,dispatchable:false};
  const detail={id:report,shop_id:shop,report_key:"pilot:testing-immutable-key",
   source_verification:"unverified",rights_verified:false,scan_enabled:false,
   items:[{listing_key:"etsy:123",title:"Cap with embroidery",image_count:2,
    rights_state:"unknown",source_verification:"unverified",sales_state:"unknown"}]};
  const reviews={items:[],latest_decision:"pending_independent_review",
    review_count_returned:0,source_verified:false,rights_verified:false,scan_enabled:false};
  const fetchSpy=vi.fn(async (url:string|URL,options?:RequestInit)=>{
    const path=new URL(String(url),"https://cam.test").pathname;
    if(options?.method==="POST"){
      expect(path).toBe(base+"/shops/"+shop+"/browser-observations/"+report+"/reviews");
      const submitted=JSON.parse(String(options.body)) as Record<string,unknown>;
      expect(submitted.decision).toBe("needs_authorized_source");
      expect(submitted.rights_basis).toBe("unknown");
      expect(submitted.note).toBe("Please verify original source and rights");
      reviews.items=[{id:"3333",reviewed_by:"second-operator",
       created_at:"2026-10-20T11:12:00Z",note:submitted.note,
       decision:submitted.decision,rights_basis:"unknown",
       evidence_reference:null,source_verified:false,scan_enabled:false}] as never;
      reviews.latest_decision="needs_authorized_source";
      return new Response(JSON.stringify({source_verified:false,rights_verified:false,scan_enabled:false}),{status:201});
    }
    const data=path.endsWith("/review-queue")?queue:
      path.endsWith("/reviews")?reviews:detail;
    return new Response(JSON.stringify(data),{status:200});
  });
  vi.stubGlobal("fetch",fetchSpy);
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT",true);
  const target=document.createElement("div");document.body.append(target);
  const root=createRoot(target);
  await act(async()=>{root.render(<BrandingDnaBrowserReviewQueue/>);});
  expect(target.textContent).toContain("Chưa được kiểm duyệt");
  expect(target.textContent).not.toContain("Start Scan");
  expect(fetchSpy).toHaveBeenCalledTimes(1);
  const item=target.querySelector<HTMLButtonElement>(".bdna-browser-reviews-list button");
  expect(item).not.toBeNull();
  await act(async()=>item?.click());
  expect(target.textContent).toContain("Cap with embroidery");
  expect(target.textContent).toContain("Quyền ảnh: unknown");
  const note=target.querySelector<HTMLTextAreaElement>("textarea");
  expect(note).not.toBeNull();
  await act(async()=>{
    if(!note)return;
    const setter=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,"value")?.set;
    setter?.call(note,"Please verify original source and rights");
    note.dispatchEvent(new Event("input",{bubbles:true}));
  });
  const form=target.querySelector<HTMLFormElement>("form");
  await act(async()=>form?.dispatchEvent(new Event("submit",{bubbles:true,cancelable:true})));
  expect(target.textContent).toContain("Đã lưu biên bản");
  expect(target.textContent).toContain("second-operator");
  expect(fetchSpy.mock.calls.filter(([,opts])=>(opts as RequestInit|undefined)?.method==="POST")).toHaveLength(1);
  await act(async()=>root.unmount());
 });

 it("blocks an unsafe API claim that a browser observation is verified",async()=>{
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT",true);
  vi.stubGlobal("fetch",vi.fn(async()=>new Response(JSON.stringify({
   items:[],count:0,limit:20,offset:0,
   source_verified:true,rights_verified:false,scan_enabled:false,dispatchable:false,
  }),{status:200})));
  const target=document.createElement("div");document.body.append(target);
  const root=createRoot(target);
  await act(async()=>{root.render(<BrandingDnaBrowserReviewQueue/>);});
  expect(target.querySelector('[role="alert"]')?.textContent).toContain("hợp đồng an toàn");
  await act(async()=>root.unmount());
 });
});
