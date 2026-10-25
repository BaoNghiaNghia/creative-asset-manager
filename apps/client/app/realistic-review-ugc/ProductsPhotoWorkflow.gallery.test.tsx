// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProductsPhotoWorkflow } from "./ProductsPhotoWorkflow";
import { runAllEcommerceJobs, type ColorwayJob, type EcommerceJob, type ColorwayQueueResult } from "./api";

vi.mock("./api", async (importOriginal) => ({
  ...await importOriginal<typeof import("./api")>(),
  runAllEcommerceJobs: vi.fn(),
}));

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

async function renderWorkflow(options:{
  colors?:ColorwayJob[],ecommerceJobs?:EcommerceJob[],
  onOpenColor?:(item:ColorwayJob)=>void,onRetryColor?:(item:ColorwayJob)=>void,
  sourcePlanId?:string,colorwayReady?:boolean,
  onQueueColors?:(retry?:boolean)=>Promise<ColorwayQueueResult>,
  onEcommerceJobChange?:()=>void|Promise<void>,
}={}) {
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
  it("View all outputs opens 23 ordered positions even when no image jobs exist",async()=>{
    const {root,host}=await renderWorkflow();
    const labels=[...host.querySelectorAll<HTMLButtonElement>(".rrugc-products-photo-footer-actions button")].map(button=>button.textContent?.trim());
    expect(labels).toEqual(["View all outputs","Run all"]);
    expect(host.textContent).not.toContain("7 skill slots");
    expect(host.textContent).not.toContain("23 outputs");
    const trigger=host.querySelector<HTMLButtonElement>('[aria-label="View all outputs"]')!;
    await act(async()=>trigger.click());
    const modal=document.querySelector<HTMLElement>('[role="dialog"][data-photo-gallery="multi"]')!;
    expect(modal).not.toBeNull();
    expect(modal.querySelectorAll(".rrugc-photo-gallery-thumb")).toHaveLength(23);
    expect(modal.textContent).toContain("0 of 23 images generated");
    expect(modal.querySelectorAll('.rrugc-photo-gallery-thumb')[0].textContent).toContain("Front High");
    expect(modal.querySelectorAll('.rrugc-photo-gallery-thumb')[4].textContent).toContain("Multi-hat 1");
    expect(modal.querySelectorAll('.rrugc-photo-gallery-thumb')[7].textContent).toContain("UGC 1");
    expect(modal.querySelectorAll('.rrugc-photo-gallery-thumb')[10].textContent).toContain("Khaki Maroon");
    await act(async()=>root.unmount());
  });

  it("Run all queues server-orchestrated Ecommerce jobs, retries colors and reports unavailable Skills",async()=>{
    vi.mocked(runAllEcommerceJobs).mockResolvedValueOnce({
      source_plan_id:"design-1",
      steps:[
        {stage:"prod_s2_ec1",status:"queued",job_id:"e1"},
        {stage:"prod_s2_ec2",status:"waiting_for_front",reason:"Will queue after Image 01."},
        {stage:"prod_s2_ec3",status:"waiting_for_front",reason:"Will queue after Image 01."},
        {stage:"prod_s2_ec4",status:"completed",job_id:"e4"},
      ],
    });
    const queueColors=vi.fn(async()=>({queued:11,existing:2,retried:0}));
    const refresh=vi.fn(async()=>undefined);
    const {root,host}=await renderWorkflow({
      sourcePlanId:"design-1",colors:colorJobs,
      colorwayReady:true,onQueueColors:queueColors,onEcommerceJobChange:refresh,
    });
    const button=host.querySelector<HTMLButtonElement>('[aria-label="Run all"]')!;
    expect(button.disabled).toBe(false);
    await act(async()=>{button.click();await Promise.resolve();});
    expect(runAllEcommerceJobs).toHaveBeenCalledWith("design-1", "natural-black");
    expect(queueColors).toHaveBeenCalledWith(true);
    expect(refresh).toHaveBeenCalledTimes(1);
    const report=host.querySelector<HTMLElement>(".rrugc-products-photo-run-all-report")!;
    expect(report.textContent).toContain("2 queued");
    expect(report.textContent).toContain("2 waiting");
    expect(report.textContent).toContain("2 blocked");
    expect(report.querySelectorAll("li")).toHaveLength(7);
    expect(report.textContent).toContain("Multi-hat");
    expect(report.textContent).toContain("UGC");
    expect(report.textContent).toContain("Skill availability is not confirmed");
    expect(button.disabled).toBe(false);
    await act(async()=>root.unmount());
  });

  it("Run all gives explicit blocked states when job APIs cannot start",async()=>{
    vi.mocked(runAllEcommerceJobs).mockRejectedValueOnce(new Error("Insufficient processing credits"));
    const {root,host}=await renderWorkflow({sourcePlanId:"design-1",colorwayReady:false});
    await act(async()=>{host.querySelector<HTMLButtonElement>('[aria-label="Run all"]')!.click();await Promise.resolve();});
    const report=host.querySelector<HTMLElement>(".rrugc-products-photo-run-all-report")!;
    expect(report.querySelectorAll("li")).toHaveLength(7);
    expect(report.textContent).toContain("7 blocked");
    expect(report.textContent).toContain("Insufficient processing credits");
    await act(async()=>root.unmount());
  });

  it("opens all 23 outputs at the clicked Ecommerce Back High image",async()=>{
    const {root,host}=await renderWorkflow({ecommerceJobs:ecommerce});
    await act(async()=>host.querySelector<HTMLButtonElement>('[aria-label="Open Back High Ecommerce output"]')!.click());
    const modal=document.querySelector<HTMLElement>('[role="dialog"][data-photo-gallery="multi"]')!;
    expect(modal).not.toBeNull();
    expect(modal.textContent).toContain("All outputs · embroidery_design.png");
    expect(modal.textContent).toContain("04 / 23");
    expect(modal.textContent).toContain("All 23 outputs");
    expect(modal.querySelector<HTMLImageElement>(".rrugc-photo-gallery-stage img")?.getAttribute("src")).toBe("/photos/back.png");
    expect(modal.querySelectorAll(".rrugc-photo-gallery-thumbnails button")).toHaveLength(23);
    await act(async()=>root.unmount());
  });
});
