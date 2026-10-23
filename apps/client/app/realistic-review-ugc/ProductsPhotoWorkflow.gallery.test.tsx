// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProductsPhotoWorkflow } from "./ProductsPhotoWorkflow";
import type { ColorwayJob, EcommerceJob } from "./api";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
afterEach(() => { document.body.replaceChildren(); document.body.style.overflow=""; });

const colorJobs: ColorwayJob[] = [
  {id:"color-0",source_plan_id:"design-1",color_key:"khaki-maroon",color_name:"Khaki Maroon",
    status:"completed",retry_count:0,attempt_count:1,error_code:null,output_url:"/photos/khaki.png"},
  {id:"color-2",source_plan_id:"design-1",color_key:"natural-brown",color_name:"Natural Brown",
    status:"failed",retry_count:1,attempt_count:2,error_code:"generation_failed",output_url:null},
  {id:"color-9",source_plan_id:"design-1",color_key:"natural-navy",color_name:"Natural Navy",
    status:"completed",retry_count:0,attempt_count:1,error_code:null,output_url:"/photos/navy.png"},
];
const ecommerce: EcommerceJob[] = [
  {id:"back",source_plan_id:"design-1",stage:"prod_s2_ec4",status:"completed",
    retry_count:0,attempt_count:1,error_code:null,output_url:"/photos/back.png",skill_name:"ecommerce-back"},
];

async function renderWorkflow(options:{colors?:ColorwayJob[],ecommerceJobs?:EcommerceJob[],onOpenColor?:(item:ColorwayJob)=>void,onRetryColor?:(item:ColorwayJob)=>void}={}) {
  const host=document.createElement("div");document.body.append(host);
  const root=createRoot(host);
  await act(async()=>root.render(<ProductsPhotoWorkflow
    sourceName="embroidery_design.png" readiness={null} {...options} />));
  return {root,host};
}

describe("Products photo output gallery navigation",()=>{
  it("opens full 13-color gallery even when clicking ungenerated color #13",async()=>{
    const {root,host}=await renderWorkflow({colors:colorJobs});
    const slots=host.querySelectorAll<HTMLButtonElement>('[data-skill="colorway"] .rrugc-colorway-slot');
    expect(slots).toHaveLength(13);
    await act(async()=>slots[12].click());
    const modal=document.querySelector<HTMLElement>('[role="dialog"][data-photo-gallery="multi"]')!;
    expect(modal).not.toBeNull();
    expect(modal.querySelectorAll(".rrugc-photo-gallery-thumb")).toHaveLength(13);
    expect(modal.querySelector(".rrugc-photo-gallery-counter")?.textContent).toBe("13 / 13");
    expect(modal.textContent).toContain("2 of 13 images generated");
    expect(modal.querySelector(".rrugc-photo-gallery-placeholder")?.textContent).toContain("Not generated");
    await act(async()=>modal.querySelector<HTMLButtonElement>('[aria-label="View output 1: Khaki Maroon · Completed"]')!.click());
    expect(modal.querySelector(".rrugc-photo-gallery-counter")?.textContent).toBe("01 / 13");
    expect(modal.querySelector<HTMLImageElement>(".rrugc-photo-gallery-stage img")?.getAttribute("src")).toBe("/photos/khaki.png");
    await act(async()=>root.unmount());
  });
  it("preserves version and retry callbacks inside all-colors modal",async()=>{
    const versions=vi.fn(),retry=vi.fn();
    const {root,host}=await renderWorkflow({colors:colorJobs,onOpenColor:versions,onRetryColor:retry});
    await act(async()=>host.querySelector<HTMLButtonElement>('[data-skill="colorway"] .rrugc-colorway-slot')!.click());
    let modal=document.querySelector<HTMLElement>('[role="dialog"][data-photo-gallery="multi"]')!;
    await act(async()=>modal.querySelector<HTMLButtonElement>(".rrugc-photo-gallery-actions button")!.click());
    expect(versions).toHaveBeenCalledWith(colorJobs[0]);
    expect(document.querySelector('[data-photo-gallery="multi"]')).toBeNull();
    await act(async()=>host.querySelectorAll<HTMLButtonElement>('[data-skill="colorway"] .rrugc-colorway-slot')[2].click());
    modal=document.querySelector<HTMLElement>('[role="dialog"][data-photo-gallery="multi"]')!;
    await act(async()=>modal.querySelector<HTMLButtonElement>(".rrugc-photo-gallery-actions button")!.click());
    expect(retry).toHaveBeenCalledWith(colorJobs[1]);
    await act(async()=>root.unmount());
  });
  it("shows Ecommerce Back High in a compact single-image output modal",async()=>{
    const {root,host}=await renderWorkflow({ecommerceJobs:ecommerce});
    await act(async()=>host.querySelector<HTMLButtonElement>('[aria-label="Open Back High Ecommerce output"]')!.click());
    const modal=document.querySelector<HTMLElement>('[role="dialog"][data-photo-gallery="single"]')!;
    expect(modal).not.toBeNull();
    expect(modal.textContent).toContain("Back High · output");
    expect(modal.querySelector<HTMLImageElement>(".rrugc-photo-gallery-stage img")?.getAttribute("src")).toBe("/photos/back.png");
    expect(modal.querySelector(".rrugc-photo-gallery-sidebar")).toBeNull();
    await act(async()=>root.unmount());
  });
});
