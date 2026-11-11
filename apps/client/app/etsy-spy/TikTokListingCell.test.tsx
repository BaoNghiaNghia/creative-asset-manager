// @vitest-environment jsdom
import {afterEach, describe, expect, it, vi} from "vitest";
import {act} from "react";
import {createRoot, type Root} from "react-dom/client";
import {TikTokListingCell} from "./TikTokListingCell";
import type {Listing} from "./api";

(globalThis as typeof globalThis & {IS_REACT_ACT_ENVIRONMENT: boolean}).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
let mount: HTMLElement | null = null;

function product(status: string): Listing {
  return {listingKey:"1234567890",pageTitle:"Mountain Sunset Sweatshirt",
    picked:true,tiktokListing:{status,draft_only:true,
      title_preview:status==="needs_fact_check"?"Mountain Sunset Sweatshirt":null},
  } as Listing;
}
function response(data: unknown) {
  return new Response(JSON.stringify(data),{status:200,
    headers:{"content-type":"application/json"}});
}
async function render(status: string, enabled=true) {
  mount=document.createElement("div");
  document.body.appendChild(mount);
  root=createRoot(mount);
  const onQueued=vi.fn();
  const fetchMock=vi.fn(async (input: RequestInfo|URL, init?: RequestInit) => {
    const uri=String(input);
    if(uri.endsWith("/tiktok-listing") && (!init || init.method!=="POST")) {
      return response({
        status,enabled,draft_only:true,ytrends_status:"unavailable",
        result:status==="needs_fact_check"?{
          title:"Mountain Sunset Sweatshirt for Outdoor Fans",
          description:"Outdoor art with the mountain at sunset and a retro-inspired color palette. Pick this design for a thoughtful present or casual day. Please compare the selected garment measurements before ordering and follow the product care label for washing.",
          highlights:["Mountain artwork","Outdoor gift","Casual shirt"],
          search_terms:["mountain sweatshirt","sunset art"],
          qa_status:"needs_fact_check",
          metrics:{title_characters:46,description_characters:230,description_words:39,
            highlights_count:3,highlights_characters_total:45,
            search_terms_count:2,search_terms_characters_total:27,
            search_terms_joined_characters:29},
        }:undefined,
      });
    }
    if((uri.endsWith("/tiktok-listing/queue")||uri.endsWith("/tiktok-listing/retry"))&&init?.method==="POST")
      return response({status:"pending",draft_only:true});
    throw Error("Unexpected request: "+uri);
  });
  vi.stubGlobal("fetch",fetchMock);
  await act(async()=>{
    root!.render(<table><tbody><tr><TikTokListingCell item={product(status)}
      canManage={true} onQueued={onQueued}/></tr></tbody></table>);
  });
  return {fetchMock,onQueued};
}
afterEach(async()=>{
  if(root)await act(async()=>root!.unmount());
  root=null;mount?.remove();mount=null;
  document.body.querySelectorAll(".etsy-tiktok-dialog-backdrop").forEach(n=>n.remove());
  vi.unstubAllGlobals();
});

describe("Picked TikTok Shop listing column",()=>{
  it("shows a compact status and opens four copyable fields with YTrends warning",async()=>{
    await render("needs_fact_check");
    const open=document.querySelector<HTMLButtonElement>(".etsy-tiktok-listing-open");
    expect(open?.textContent).toContain("Review copy");
    await act(async()=>{open?.click()});
    const dialog=document.querySelector('[role="dialog"]');
    expect(dialog?.textContent).toContain("Product Title");
    expect(dialog?.textContent).toContain("Product Description");
    expect(dialog?.textContent).toContain("Product Highlights");
    expect(dialog?.textContent).toContain("Search Terms");
    expect(dialog?.textContent).toContain("Không khả dụng");
    expect(dialog?.textContent).toContain("Cần kiểm tra");
    expect(dialog?.querySelectorAll(".etsy-tiktok-copy-title button")).toHaveLength(4);
    expect(dialog?.textContent).not.toContain("Published");
  });
  it("queues one Picked skill job only on an explicit click",async()=>{
    const {fetchMock,onQueued}=await render("not_queued");
    const open=document.querySelector<HTMLButtonElement>(".etsy-tiktok-listing-open");
    await act(async()=>{open?.click()});
    expect(fetchMock.mock.calls.filter(call=>call[1]?.method==="POST")).toHaveLength(0);
    const run=Array.from(document.querySelectorAll<HTMLButtonElement>(".etsy-tiktok-dialog-actions button"))
      .find(btn=>btn.textContent?.includes("Run listing skill"));
    await act(async()=>{run?.click()});
    expect(fetchMock.mock.calls.filter(call=>call[1]?.method==="POST")).toHaveLength(1);
    expect(onQueued).toHaveBeenCalledTimes(1);
  });
  it("offers retry for a failed job instead of reusing a terminal queue request",async()=>{
    const {fetchMock,onQueued}=await render("failed");
    await act(async()=>{
      document.querySelector<HTMLButtonElement>(".etsy-tiktok-listing-open")?.click();
    });
    const retry=Array.from(document.querySelectorAll<HTMLButtonElement>(".etsy-tiktok-dialog-actions button"))
      .find(button=>button.textContent?.includes("Retry failed draft"));
    expect(retry).toBeDefined();
    await act(async()=>{retry?.click()});
    expect(fetchMock.mock.calls.some(call=>
      String(call[0]).endsWith("/tiktok-listing/retry") && call[1]?.method==="POST")).toBe(true);
    expect(onQueued).toHaveBeenCalledTimes(1);
  });
  it("cannot offer a run action when feature flag is OFF",async()=>{
    await render("not_queued",false);
    const open=document.querySelector<HTMLButtonElement>(".etsy-tiktok-listing-open");
    await act(async()=>{open?.click()});
    const dialog=document.querySelector('[role="dialog"]');
    expect(dialog?.textContent).toContain("Chưa có bản nháp");
    expect(dialog?.textContent).not.toContain("Run listing skill");
  });
});