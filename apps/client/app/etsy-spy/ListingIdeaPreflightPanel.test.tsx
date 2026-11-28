// @vitest-environment jsdom
import {afterEach, expect, it, vi} from "vitest";
import {act} from "react";
import {createRoot, type Root} from "react-dom/client";
import {ListingIdeaPreflightPanel} from "./ListingIdeaPreflightPanel";
(globalThis as typeof globalThis & {IS_REACT_ACT_ENVIRONMENT:boolean}).IS_REACT_ACT_ENVIRONMENT=true;
let root:Root|null=null;
let mount:HTMLElement|null=null;
const empty = {designs:[],selected_design:null,saved:{},seo:{status:"not_queued"},
  video_ugc:{count:0,status:"not_queued"},video_links:[],listing_image_count:0,
  blockers:["google_drive_video_links_missing","sms_template_selection_required"],
  warnings:[],ready_for_sms_payload_review:false};
afterEach(async()=>{if(root)await act(async()=>root!.unmount());mount?.remove();root=null;mount=null;vi.unstubAllGlobals()});
async function render(canManage:boolean){
  const mock=vi.fn(async (_input:RequestInfo|URL,init?:RequestInit)=>{
    const data=init?.method==="PUT"?{...empty,saved:JSON.parse(String(init.body))}:empty;
    return new Response(JSON.stringify(data),{headers:{"content-type":"application/json"}});
  });
  vi.stubGlobal("fetch",mock);
  mount=document.createElement("div");document.body.appendChild(mount);root=createRoot(mount);
  await act(async()=>root!.render(<ListingIdeaPreflightPanel listingKey="1234567890" canManage={canManage}/>));
  const button=(text:string)=>Array.from(mount!.querySelectorAll("button")).find(b=>b.textContent===text)!;
  expect(mock).not.toHaveBeenCalled();
  await act(async()=>button("Kiểm tra data").click());
  expect(mock).toHaveBeenCalledTimes(1);
  expect(mount.textContent).toContain("Link Google Drive video");
  return {mock,button};
}
it("loads only on demand and saves draft to CAM without SMS submit",async()=>{
  const {mock,button}=await render(true);
  await act(async()=>button("Lưu dữ liệu CAM").click());
  expect(mock).toHaveBeenCalledTimes(2);
  const [url,init]=mock.mock.calls[1];
  expect(String(url)).toContain("/1234567890/listing-idea-preflight");
  expect(init?.method).toBe("PUT");
  expect(mount!.textContent).toContain("Chưa gửi SMS");
});
it("read-only role sees missing fields but cannot save",async()=>{
  const {mock,button}=await render(false);
  expect(button("Lưu dữ liệu CAM")).toBeUndefined();
  expect(mount!.querySelector("fieldset")?.disabled).toBe(true);
  expect(mock).toHaveBeenCalledTimes(1);
});
