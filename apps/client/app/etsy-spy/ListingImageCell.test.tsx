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
       previewUrl:"/api/v1/etsy-spy/listing-images/"+pack+"/1/preview",
       originalUrl:"/api/v1/etsy-spy/listing-images/"+pack+"/1/original"},
      {packId:pack,slot:2,sha256:"b".repeat(64),designSha256:"b".repeat(64),
       placement:"front_center",manifestReady:false,width:2000,height:2000,
       savedAt:"2026-11-17T01:01:00Z",
       thumbnailUrl:"/api/v1/etsy-spy/listing-images/"+pack+"/2/thumbnail",
       previewUrl:"/api/v1/etsy-spy/listing-images/"+pack+"/2/preview",
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
    // Only a temporary image skeleton may be present, never a caption.
    expect(main?.querySelectorAll("span:not(.etsy-image-skeleton)")).toHaveLength(0);
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
  it("shows 1254px completed previews for review without claiming final QA",async()=>{
    const lowResGallery:ListingImageGallery={
      ...gallery,groups:gallery.groups.map(group=>({
        ...group,images:group.images.map((image,i)=>i===0
          ?{...image,width:1254,height:1254,previewOnly:true,manifestReady:false}
          :{...image,previewOnly:false}),
      })),
    };
    spy.mockResolvedValue(lowResGallery);
    mount({...item,listingImages:[{
      garmentCode:"C1717",count:2,reviewCount:1,
      mainThumbnailUrl:"/main-preview.png",
    }]});
    const badge=container.querySelector<HTMLButtonElement>(".etsy-listing-image-badge");
    expect(badge?.textContent).toContain("QA");
    expect(badge?.title).toContain("1 ảnh cần QA");
    await act(async()=>{badge?.click();});
    const dialog=document.querySelector<HTMLElement>('[role="dialog"]');
    expect(dialog?.querySelector(".etsy-listing-image-review-notice")?.textContent)
      .toContain("Cần QA · 1254×1254");
    expect(dialog?.querySelectorAll(".etsy-listing-image-tile-review")).toHaveLength(1);
    expect(dialog?.querySelector(".etsy-listing-image-review-frame img")
      ?.getAttribute("src")).toContain("/1/preview");
    await act(async()=>{
      dialog?.querySelectorAll<HTMLButtonElement>(".etsy-listing-image-tile")[1]?.click();
    });
    expect(dialog?.querySelector(".etsy-listing-image-review-notice")).toBeNull();
    expect(spy).toHaveBeenCalledTimes(1);
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
    expect(dialog?.querySelector(".etsy-listing-image-dialog-head h2")?.textContent).toBe("Listing Image");
    expect(dialog?.textContent).not.toContain("Ảnh xem trước");
    expect(dialog?.textContent).not.toContain("Ảnh theo sản phẩm");
    expect(dialog?.textContent).not.toContain("ảnh đã lưu");
    expect(dialog?.textContent).not.toContain("Design");
    expect(dialog?.textContent).not.toContain(item.pageTitle);
    expect(dialog?.querySelector(".etsy-listing-image-foot")).toBeNull();
    expect(dialog?.querySelector(".etsy-listing-image-design-title")).toBeNull();
    expect(dialog?.querySelectorAll(".etsy-listing-image-group")).toHaveLength(1);
    expect(dialog?.querySelector(".etsy-listing-image-group-heading")?.textContent).toContain("C1717");
    const tiles = Array.from(dialog?.querySelectorAll<HTMLButtonElement>(".etsy-listing-image-tile") || []);
    expect(tiles).toHaveLength(2);
    expect(tiles[0]?.getAttribute("aria-pressed")).toBe("true");
    const previewImage = () => dialog?.querySelector<HTMLImageElement>(".etsy-listing-image-review-frame img");
    expect(previewImage()?.getAttribute("src")).toContain("/1/preview");
    expect(previewImage()?.getAttribute("src")).not.toContain("/original");
    expect(previewImage()?.getAttribute("data-review-quality")).toBe("webp");
    expect(dialog?.querySelectorAll(".etsy-listing-image-review-frame img")).toHaveLength(1);
    await act(async()=>{tiles[1]?.click();});
    expect(previewImage()?.getAttribute("src")).toContain("/2/preview");
    expect(tiles[1]?.getAttribute("aria-pressed")).toBe("true");
    expect(tiles[0]?.getAttribute("aria-pressed")).toBe("false");
    expect(dialog?.querySelector<HTMLAnchorElement>(".etsy-listing-image-fullsize")?.href)
      .toContain("/2/original");
    expect(dialog?.querySelector<HTMLAnchorElement>(".etsy-listing-image-fullsize")?.getAttribute("title"))
      .toBe("Mở PNG gốc");
    expect(dialog?.querySelectorAll('img[src*="/original"]')).toHaveLength(0);
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
      previewUrl:"/g180/" + slot + "/preview",
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
    expect(garmentTiles.map(tile=>tile.textContent?.trim())).toEqual(["03","10"]);
    await act(async()=>{garmentTiles[1].click();});
    expect(dialog?.querySelector<HTMLImageElement>(".etsy-listing-image-review-frame img")?.getAttribute("src"))
      .toBe("/g180/10/preview");
    expect(dialog?.querySelector(".etsy-listing-image-fullsize")?.getAttribute("aria-label"))
      .toContain("G180");
    expect(garmentTiles[1].getAttribute("aria-pressed")).toBe("true");
    expect(groups[0].querySelector(".etsy-listing-image-tile.is-selected")).toBeNull();
    expect(spy).toHaveBeenCalledTimes(1);
  });
  it("reuses gallery metadata on quick reopen but refreshes on badge changes",async()=>{
    spy.mockResolvedValue(gallery);
    mount();
    const trigger=document.querySelector<HTMLButtonElement>(".etsy-listing-image-badge")!;
    await act(async()=>{trigger.click();});
    expect(spy).toHaveBeenCalledTimes(1);
    await act(async()=>{
      document.querySelector<HTMLButtonElement>(".etsy-listing-image-close")?.click();
    });
    await act(async()=>{trigger.click();});
    expect(spy).toHaveBeenCalledTimes(1);
    expect(document.querySelector(".etsy-listing-image-review-frame")).not.toBeNull();
  });
  it("falls back to a small thumbnail instead of downloading full PNG when WebP fails",async()=>{
    spy.mockResolvedValue(gallery);
    mount();
    await act(async()=>{
      document.querySelector<HTMLButtonElement>(".etsy-listing-image-badge")?.click();
    });
    const review=document.querySelector<HTMLImageElement>(".etsy-listing-image-review-frame img")!;
    expect(review.src).toContain("/preview");
    await act(async()=>{review.dispatchEvent(new Event("error", {bubbles:true}));});
    expect(review.src).toContain("/thumbnail");
    expect(review.src).not.toContain("/original");
  });
  it("zooms at the wheel cursor and resets on middle click instead of opening the PNG", async()=>{
    spy.mockResolvedValue(gallery);
    mount();
    await act(async()=>{
      document.querySelector<HTMLButtonElement>(".etsy-listing-image-badge")?.click();
    });
    const frame=document.querySelector<HTMLElement>(".etsy-listing-image-review-frame")!;
    const link=frame.querySelector<HTMLAnchorElement>(".etsy-listing-image-fullsize")!;
    const preview=frame.querySelector<HTMLImageElement>("img")!;
    vi.spyOn(frame,"getBoundingClientRect").mockReturnValue({
      x:50,y:70,left:50,top:70,right:550,bottom:570,width:500,height:500,
      toJSON:()=>({}),
    });
    const wheel=new WheelEvent("wheel",{
      bubbles:true,cancelable:true,deltaY:-200,clientX:430,clientY:160,
    });
    await act(async()=>{frame.dispatchEvent(wheel);});
    expect(wheel.defaultPrevented).toBe(true);
    expect(Number(frame.dataset.zoomScale)).toBeGreaterThan(1);
    const match=preview.style.transform.match(/translate\(([-\d.]+)px, ([-\d.]+)px\) scale\(([-\d.]+)\)/);
    expect(match).not.toBeNull();
    const scale=Number(match?.[3]);
    // The pixel at the pointer remains at the same screen position.
    expect(Number(match?.[1])).toBeCloseTo(130*(1-scale),5);
    expect(Number(match?.[2])).toBeCloseTo(-160*(1-scale),5);
    expect(preview.src).toContain("/preview");
    expect(preview.src).not.toContain("/original");

    const down=new MouseEvent("mousedown",{bubbles:true,cancelable:true,button:1});
    const middle=new MouseEvent("auxclick",{bubbles:true,cancelable:true,button:1});
    await act(async()=>{link.dispatchEvent(down);link.dispatchEvent(middle);});
    expect(down.defaultPrevented).toBe(true);
    expect(middle.defaultPrevented).toBe(true);
    expect(frame.dataset.zoomScale).toBe("1.000");
    expect(preview.style.transform).toBe("translate(0px, 0px) scale(1)");
    expect(link.getAttribute("href")).toContain("/original");
    expect(spy).toHaveBeenCalledTimes(1);
  });
  it("keeps product list wheel independent and fits each newly selected image", async()=>{
    spy.mockResolvedValue(gallery);
    mount();
    await act(async()=>{
      document.querySelector<HTMLButtonElement>(".etsy-listing-image-badge")?.click();
    });
    const firstFrame=document.querySelector<HTMLElement>(".etsy-listing-image-review-frame")!;
    vi.spyOn(firstFrame,"getBoundingClientRect").mockReturnValue({
      x:0,y:0,left:0,top:0,right:400,bottom:400,width:400,height:400,
      toJSON:()=>({}),
    });
    await act(async()=>{
      firstFrame.dispatchEvent(new WheelEvent("wheel",{
        bubbles:true,cancelable:true,deltaY:-240,clientX:290,clientY:220,
      }));
    });
    expect(Number(firstFrame.dataset.zoomScale)).toBeGreaterThan(1);
    const side=document.querySelector<HTMLElement>(".etsy-listing-image-library")!;
    const sideWheel=new WheelEvent("wheel",{bubbles:true,cancelable:true,deltaY:200});
    await act(async()=>{side.dispatchEvent(sideWheel);});
    expect(sideWheel.defaultPrevented).toBe(false);
    expect(Number(firstFrame.dataset.zoomScale)).toBeGreaterThan(1);
    await act(async()=>{
      document.querySelectorAll<HTMLButtonElement>(".etsy-listing-image-tile")[1]?.click();
    });
    const newFrame=document.querySelector<HTMLElement>(".etsy-listing-image-review-frame")!;
    expect(newFrame).not.toBe(firstFrame);
    expect(newFrame.dataset.zoomScale).toBe("1.000");
    expect(newFrame.querySelector<HTMLImageElement>("img")?.src).toContain("/2/preview");
    expect(spy).toHaveBeenCalledTimes(1);
  });
  it("pans an enlarged preview by dragging without following the original link", async()=>{
    spy.mockResolvedValue(gallery);
    mount();
    await act(async()=>{
      document.querySelector<HTMLButtonElement>(".etsy-listing-image-badge")?.click();
    });
    const frame=document.querySelector<HTMLElement>(".etsy-listing-image-review-frame")!;
    const link=frame.querySelector<HTMLAnchorElement>("a")!;
    vi.spyOn(frame,"getBoundingClientRect").mockReturnValue({
      x:0,y:0,left:0,top:0,right:400,bottom:400,width:400,height:400,
      toJSON:()=>({}),
    });
    await act(async()=>{
      frame.dispatchEvent(new WheelEvent("wheel",{
        bubbles:true,cancelable:true,deltaY:-300,clientX:250,clientY:200,
      }));
    });
    const before=frame.querySelector<HTMLImageElement>("img")!.style.transform;
    await act(async()=>{
      link.dispatchEvent(new MouseEvent("pointerdown",{
        bubbles:true,cancelable:true,button:0,clientX:250,clientY:200,
      }));
      link.dispatchEvent(new MouseEvent("pointermove",{
        bubbles:true,cancelable:true,button:0,clientX:280,clientY:230,
      }));
      link.dispatchEvent(new MouseEvent("pointerup",{
        bubbles:true,cancelable:true,button:0,clientX:280,clientY:230,
      }));
    });
    const after=frame.querySelector<HTMLImageElement>("img")!.style.transform;
    expect(after).not.toBe(before);
    const click=new MouseEvent("click",{bubbles:true,cancelable:true,button:0});
    act(()=>{link.dispatchEvent(click);});
    expect(click.defaultPrevented).toBe(true);
  });
  it("shows neutral empty value when no saved output exists",()=>{
    mount({...item,listingImages:[]});
    expect(document.querySelector(".etsy-listing-image-badge")).toBeNull();
    expect(spy).not.toHaveBeenCalled();
  });
});
