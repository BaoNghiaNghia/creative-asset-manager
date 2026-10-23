// @vitest-environment jsdom
import { act, type ComponentProps } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OutputPhotoGalleryModal, type PhotoOutputItem } from "./OutputPhotoGalleryModal";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
afterEach(() => { document.body.replaceChildren(); document.body.style.overflow = ""; });

const colors: PhotoOutputItem[] = Array.from({length:13},(_,i)=>({
  key:"hat-"+i,label:"Hat Color "+(i+1),
  src: i===0 || i===9 ? "/output/"+i+".png" : null,
  status: i===0 || i===9 ? "completed" : i===2 ? "failed" : i===3 ? "queued" : "pending",
}));

async function mount(props: Partial<ComponentProps<typeof OutputPhotoGalleryModal>> = {}) {
  const host=document.createElement("div");document.body.append(host);
  const root=createRoot(host);
  const close=vi.fn();
  await act(async()=>root.render(<OutputPhotoGalleryModal title="13 Hat Colors" items={colors} onClose={close} {...props}/>));
  return {root,close};
}

describe("Compact photo output gallery",()=>{
  it("opens 13 persistent slot positions with missing, failed, queued and complete states",async()=>{
    const {root}=await mount({initialIndex:0});
    const dialog=document.querySelector<HTMLElement>('[role="dialog"][data-photo-gallery="multi"]')!;
    expect(dialog).not.toBeNull();
    const thumbs=dialog.querySelectorAll<HTMLButtonElement>(".rrugc-photo-gallery-thumb");
    expect(thumbs).toHaveLength(13);
    expect(dialog.textContent).toContain("2 of 13 images generated");
    expect(thumbs[0].getAttribute("aria-pressed")).toBe("true");
    expect(dialog.querySelector<HTMLImageElement>(".rrugc-photo-gallery-stage img")?.getAttribute("src")).toBe("/output/0.png");
    await act(async()=>thumbs[5].click());
    expect(dialog.textContent).toContain("No output has been generated for this slot yet.");
    expect(dialog.querySelector(".rrugc-photo-gallery-stage img")).toBeNull();
    expect(dialog.querySelectorAll(".rrugc-photo-gallery-thumb")).toHaveLength(13);
    await act(async()=>root.unmount());
  });

  it("starts at the clicked thumbnail, navigates through missing outputs by arrows and keyboard",async()=>{
    const {root}=await mount({initialIndex:9});
    const dialog=document.querySelector<HTMLElement>('[role="dialog"]')!;
    expect(dialog.querySelector(".rrugc-photo-gallery-counter")?.textContent).toBe("10 / 13");
    expect(dialog.querySelector<HTMLImageElement>(".rrugc-photo-gallery-stage img")?.src).toContain("/output/9.png");
    await act(async()=>document.dispatchEvent(new KeyboardEvent("keydown",{key:"ArrowRight",bubbles:true})));
    expect(dialog.querySelector(".rrugc-photo-gallery-counter")?.textContent).toBe("11 / 13");
    expect(dialog.querySelector(".rrugc-photo-gallery-stage img")).toBeNull();
    await act(async()=>dialog.querySelector<HTMLButtonElement>('[aria-label="Previous output image"]')!.click());
    expect(dialog.querySelector(".rrugc-photo-gallery-counter")?.textContent).toBe("10 / 13");
    await act(async()=>root.unmount());
  });

  it("offers per-color version history and controlled failed-job retry in the gallery",async()=>{
    const versions=vi.fn(),retry=vi.fn(),disabled=vi.fn((item:PhotoOutputItem)=>item.key==="hat-2");
    const {root}=await mount({onOpenVersions:versions,onRetry:retry,retryDisabled:disabled});
    let dialog=document.querySelector<HTMLElement>('[role="dialog"]')!;
    await act(async()=>dialog.querySelector<HTMLButtonElement>('[aria-label="View output 3: Hat Color 3 · Failed"]')!.click());
    const retryButton=dialog.querySelector<HTMLButtonElement>(".rrugc-photo-gallery-actions button")!;
    expect(retryButton.textContent).toContain("Retry");
    expect(retryButton.disabled).toBe(true);
    await act(async()=>dialog.querySelector<HTMLButtonElement>('[aria-label="View output 10: Hat Color 10 · Completed"]')!.click());
    const versionButton=Array.from(dialog.querySelectorAll<HTMLButtonElement>(".rrugc-photo-gallery-actions button")).find(x=>x.textContent?.includes("View versions"))!;
    await act(async()=>versionButton.click());
    expect(versions).toHaveBeenCalledWith(colors[9]);
    expect(retry).not.toHaveBeenCalled();
    await act(async()=>root.unmount());
  });

  it("uses bounded single-image layout and restores body scroll when closed",async()=>{
    document.body.style.overflow="auto";
    const {root,close}=await mount({
      title:"Back High · output",items:[colors[0]],initialIndex:0,
    });
    const dialog=document.querySelector<HTMLElement>('[role="dialog"][data-photo-gallery="single"]')!;
    expect(dialog).not.toBeNull();
    expect(dialog.querySelector(".rrugc-photo-gallery-sidebar")).toBeNull();
    expect(dialog.textContent).toContain("1 of 1 image generated");
    expect(dialog.querySelector<HTMLImageElement>(".rrugc-photo-gallery-stage img")?.src).toContain("/output/0.png");
    expect(document.body.style.overflow).toBe("hidden");
    await act(async()=>document.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true})));
    expect(close).toHaveBeenCalledTimes(1);
    await act(async()=>root.unmount());
    expect(document.body.style.overflow).toBe("auto");
  });
});
