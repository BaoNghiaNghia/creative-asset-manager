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
      {packId:pack,slot:1,sha256:"a".repeat(64),designSha256:"b".repeat(64),
       placement:"front_center",manifestReady:false,width:2000,height:2000,
       savedAt:"2026-11-17T01:00:00Z",
       thumbnailUrl:"/api/v1/etsy-spy/listing-images/"+pack+"/1/thumbnail",
       originalUrl:"/api/v1/etsy-spy/listing-images/"+pack+"/1/original"},
      {packId:pack,slot:2,sha256:"b".repeat(64),designSha256:"b".repeat(64),
       placement:"front_center",manifestReady:false,width:2000,height:2000,
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
  it("keeps Main in the left track and badges in the right track",()=>{
    const mainRecord = {...item, listingImages: [
      {garmentCode:"G180",count:6},
      {garmentCode:"C1717",count:10,mainThumbnailUrl:"/main-c1717.png"},
    ]} as Listing;
    mount(mainRecord);
    const layout = container.querySelector(".etsy-listing-image-layout");
    expect(layout).not.toBeNull();
    expect(layout?.children).toHaveLength(2);
    expect(layout?.firstElementChild?.classList.contains("etsy-listing-image-primary")).toBe(true);
    expect(layout?.lastElementChild?.classList.contains("etsy-listing-image-badges")).toBe(true);
    const main = layout?.querySelector<HTMLButtonElement>(".etsy-listing-image-main");
    expect(main?.parentElement).toBe(layout?.firstElementChild);
    expect(main?.querySelector("img")?.getAttribute("src")).toBe("/main-c1717.png");
    expect(main?.querySelectorAll("img")).toHaveLength(1);
    expect(main?.querySelector("span")).toBeNull();
    expect(main?.textContent?.trim()).toBe("");
    expect(main?.getAttribute("aria-label")).toContain("Main C1717");
    const badges = Array.from(layout?.querySelectorAll(".etsy-listing-image-badge") || []);
    expect(badges.map(button => button.textContent?.trim())).toEqual([
      "10 C1717", "6 G180",
    ]);
    expect(badges.every(button => button.parentElement === layout?.lastElementChild)).toBe(true);
    expect(spy).not.toHaveBeenCalled();
  });
  it("keeps the placeholder left and all four model badges to the right",()=>{
    mount({...item, listingImages:[
      {garmentCode:"G185",count:2},{garmentCode:"G500",count:3},
      {garmentCode:"G180",count:6},{garmentCode:"C1717",count:10},
    ]});
    const layout = container.querySelector(".etsy-listing-image-layout");
    expect(layout?.querySelector(".etsy-listing-image-primary .etsy-listing-image-pending")?.textContent)
      .toBe("Chưa có ảnh");
    expect(layout?.querySelector(".etsy-listing-image-main")).toBeNull();
    const badges = Array.from(layout?.querySelectorAll(".etsy-listing-image-badges button") || []);
    expect(badges.map(button => button.textContent?.trim())).toEqual([
      "10 C1717", "6 G180", "2 G185", "3 G500",
    ]);
    expect(layout?.querySelector(".etsy-listing-image-badges")?.parentElement).toBe(layout);
  });
  it("displays counts by garment without polling all listings",()=>{
    mount();
    expect(document.querySelectorAll(".etsy-listing-image-badge")).toHaveLength(2);
    expect(document.body.textContent).toContain("10 C1717");
    expect(document.body.textContent).toContain("4 G500");
    expect(spy).not.toHaveBeenCalled();
  });
  it("splits gallery into left review and right product images, switching the preview without another fetch",async()=>{
    spy.mockResolvedValue(gallery);
    mount();
    await act(async()=>{(document.querySelector(".etsy-listing-image-badge") as HTMLButtonElement).click();});
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy.mock.calls[0][0]).toBe("/listings/1234567890/listing-images");
    const dialog = document.querySelector<HTMLElement>('[role="dialog"]');
    expect(dialog).not.toBeNull();
    const workspace = dialog?.querySelector(".etsy-listing-image-workspace");
    expect(workspace?.children).toHaveLength(2);
    expect(workspace?.firstElementChild?.classList.contains("etsy-listing-image-preview")).toBe(true);
    expect(workspace?.lastElementChild?.classList.contains("etsy-listing-image-library")).toBe(true);
    expect(dialog?.textContent).toContain("Ảnh xem trước");
    expect(dialog?.textContent).toContain("Ảnh theo sản phẩm");
    expect(dialog?.textContent).toContain("2 ảnh đã lưu");
    expect(dialog?.querySelectorAll(".etsy-listing-image-group")).toHaveLength(1);
    expect(dialog?.querySelector(".etsy-listing-image-group-heading")?.textContent).toContain("C1717");
    const tiles = Array.from(dialog?.querySelectorAll<HTMLButtonElement>(".etsy-listing-image-tile") || []);
    expect(tiles).toHaveLength(2);
    expect(tiles[0]?.getAttribute("aria-pressed")).toBe("true");
    const previewImage = () => dialog?.querySelector<HTMLImageElement>(".etsy-listing-image-review-frame img");
    expect(previewImage()?.getAttribute("src")).toContain("/1/original");
    expect(dialog?.querySelectorAll(".etsy-listing-image-review-frame img")).toHaveLength(1);
    await act(async()=>{tiles[1]?.click();});
    expect(previewImage()?.getAttribute("src")).toContain("/2/original");
    expect(tiles[1]?.getAttribute("aria-pressed")).toBe("true");
    expect(tiles[0]?.getAttribute("aria-pressed")).toBe("false");
    expect(dialog?.querySelector<HTMLAnchorElement>(".etsy-listing-image-preview-meta a")?.href)
      .toContain("/2/original");
    expect(spy).toHaveBeenCalledTimes(1);
    act(()=>window.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true})));
    expect(document.querySelector('[role="dialog"]')).toBeNull();
  });
  it("groups C1717 and G180 in the right library, orders slots and previews selected garment",async()=>{
    const otherPack = "sp-" + "c".repeat(40);
    const otherImages = [10, 3].map(slot=>({
      ...gallery.groups[0].images[0],
      slot, packId:otherPack, sha256:String(slot).repeat(64),
      thumbnailUrl:"/g180/" + slot + "/thumbnail",
      originalUrl:"/g180/" + slot + "/original",
    }));
    spy.mockResolvedValue({
      ...gallery, total:4,
      groups:[gallery.groups[0], {garmentCode:"G180",count:2,images:otherImages}],
    });
    mount();
    await act(async()=>{
      (document.querySelector(".etsy-listing-image-badge") as HTMLButtonElement).click();
    });
    const dialog=document.querySelector('[role="dialog"]');
    const groups=Array.from(dialog?.querySelectorAll(".etsy-listing-image-group") || []);
    expect(groups).toHaveLength(2);
    expect(groups.map(node=>node.querySelector("h3")?.textContent)).toEqual(["C1717","G180"]);
    const garmentTiles=Array.from(groups[1].querySelectorAll<HTMLButtonElement>(".etsy-listing-image-tile"));
    expect(garmentTiles.map(tile=>tile.textContent?.trim())).toEqual(["Ảnh 03","Ảnh 10"]);
    await act(async()=>{garmentTiles[1].click();});
    expect(dialog?.querySelector<HTMLImageElement>(".etsy-listing-image-review-frame img")?.getAttribute("src"))
      .toBe("/g180/10/original");
    expect(dialog?.querySelector(".etsy-listing-image-pane-heading")?.textContent)
      .toContain("G180");
    expect(garmentTiles[1].getAttribute("aria-pressed")).toBe("true");
    expect(groups[0].querySelector(".etsy-listing-image-tile.is-selected")).toBeNull();
    expect(spy).toHaveBeenCalledTimes(1);
  });
  it("shows neutral empty value when no saved output exists",()=>{
    mount({...item,listingImages:[]});
    expect(document.querySelector(".etsy-listing-image-badge")).toBeNull();
    expect(spy).not.toHaveBeenCalled();
  });
});
