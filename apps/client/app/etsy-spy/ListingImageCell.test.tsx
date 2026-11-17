// @vitest-environment jsdom
import {afterEach,describe,expect,it,vi} from "vitest";
import {act} from "react";
import {createRoot,type Root} from "react-dom/client";
import {ListingImageCell} from "./ListingImageCell";
import type {Listing,ListingImageGallery} from "./api";

vi.mock("./api", async original => {
  const rest=await original<typeof import("./api")>();
  return {...rest,spyFetch:vi.fn()};
});
import {spyFetch} from "./api";
const spy=vi.mocked(spyFetch);
(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT:boolean }).IS_REACT_ACT_ENVIRONMENT=true;
const pack="sp-"+"a".repeat(40);
const item={listingKey:"1234567890",pageTitle:"Cat Mom Shirt",picked:true,
  listingImages:[{garmentCode:"C1717",count:10},{garmentCode:"G500",count:4}]} as Listing;
const gallery:ListingImageGallery={
  listingKey:"1234567890",total:2,groups:[
    {garmentCode:"C1717",count:2,images:[
      {packId:pack,slot:1,sha256:"a".repeat(64),width:2000,height:2000,
       savedAt:"2026-11-17T01:00:00Z",
       thumbnailUrl:"/api/v1/etsy-spy/listing-images/"+pack+"/1/thumbnail",
       originalUrl:"/api/v1/etsy-spy/listing-images/"+pack+"/1/original"},
      {packId:pack,slot:2,sha256:"b".repeat(64),width:2000,height:2000,
       savedAt:"2026-11-17T01:01:00Z",
       thumbnailUrl:"/api/v1/etsy-spy/listing-images/"+pack+"/2/thumbnail",
       originalUrl:"/api/v1/etsy-spy/listing-images/"+pack+"/2/original"}
    ]}
  ],
};
let root:Root,container:HTMLDivElement;
function mount(record:Listing=item) {
  container=document.createElement("div");document.body.append(container);
  root=createRoot(container);
  act(()=>root.render(<table><tbody><tr><ListingImageCell item={record}/></tr></tbody></table>));
}
afterEach(()=>{act(()=>root?.unmount());container?.remove();
  document.querySelectorAll(".etsy-listing-image-overlay").forEach(node=>node.remove());spy.mockReset();});
describe("Listing Image saved output badges",()=>{
  it("displays counts by garment without polling all listings",()=>{
    mount();
    expect(document.querySelectorAll(".etsy-listing-image-badge")).toHaveLength(2);
    expect(document.body.textContent).toContain("10 C1717");
    expect(document.body.textContent).toContain("4 G500");
    expect(spy).not.toHaveBeenCalled();
  });
  it("fetches per-listing gallery only on open, shows saved count and previews",async()=>{
    spy.mockResolvedValue(gallery);
    mount();
    await act(async()=>{(document.querySelector(".etsy-listing-image-badge") as HTMLButtonElement).click();});
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy.mock.calls[0][0]).toBe("/listings/1234567890/listing-images");
    expect(document.querySelector('[role="dialog"]')).not.toBeNull();
    expect(document.body.textContent).toContain("2 ảnh output đã lưu");
    expect(document.querySelectorAll(".etsy-listing-image-tile")).toHaveLength(2);
    expect(document.querySelectorAll(".etsy-listing-image-large img")).toHaveLength(1);
    act(()=>window.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true})));
    expect(document.querySelector('[role="dialog"]')).toBeNull();
  });
  it("shows neutral empty value when no saved output exists",()=>{
    mount({...item,listingImages:[]});
    expect(document.querySelector(".etsy-listing-image-badge")).toBeNull();
    expect(spy).not.toHaveBeenCalled();
  });
});
