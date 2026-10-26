// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProductsPhotoWorkflow } from "./ProductsPhotoWorkflow";
import { runAllEcommerceJobs, type ColorwayJob, type EcommerceJob, type ColorwayQueueResult } from "./api";

vi.mock("./api", async (importOriginal) => ({
  ...await importOriginal<typeof import("./api")>(),
  runAllEcommerceJobs: vi.fn(),
  getEcommerceRunAllStatus: vi.fn().mockResolvedValue(null),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
afterEach(() => { document.body.replaceChildren(); document.body.style.overflow=""; });

const colorJobs: ColorwayJob[] = [
  {id:"color-0",source_plan_id:"design-1",color_key:"natural-black",color_name:"Natural Black",
    status:"completed",retry_count:0,attempt_count:1,error_code:null,output_url:"/photos/khaki.png"},
  {id:"color-2",source_plan_id:"design-1",color_key:"natural-brown",color_name:"Natural Brown",
    status:"failed",retry_count:1,attempt_count:2,error_code:"generation_failed",output_url:null},
  {id:"color-9",source_plan_id:"design-1",color_key:"natural-navy",color_name:"Natural Navy",
    status:"completed",retry_count:0,attempt_count:1,error_code:null,output_url:"/photos/navy.png"},
  // Retired historical result exists in the API/DB but may never appear in Slot 7.
  {id:"legacy-khaki",source_plan_id:"design-1",color_key:"khaki-maroon",color_name:"Khaki Maroon",
    status:"completed",retry_count:0,attempt_count:1,error_code:null,output_url:"/photos/old-hat.png"},
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
  it("opens full 12-color gallery even when clicking ungenerated color #12",async()=>{
    const {root,host}=await renderWorkflow({colors:colorJobs});
    const slots=host.querySelectorAll<HTMLButtonElement>('[data-skill="colorway"] .rrugc-colorway-slot');
    expect(slots).toHaveLength(12);
    await act(async()=>slots[11].click());
    const modal=document.querySelector<HTMLElement>('[role="dialog"][data-photo-gallery="multi"]')!;
    expect(modal).not.toBeNull();
    expect(modal.querySelectorAll(".rrugc-photo-gallery-thumb")).toHaveLength(12);
    expect(modal.querySelector(".rrugc-photo-gallery-counter")?.textContent).toBe("12 / 12");
    expect(modal.textContent).toContain("2 of 12 images generated");
    expect(modal.textContent).not.toContain("Khaki Maroon");
    expect(modal.innerHTML).not.toContain("/photos/old-hat.png");
    expect(modal.querySelector(".rrugc-photo-gallery-placeholder")?.textContent).toContain("Not generated");
    await act(async()=>modal.querySelector<HTMLButtonElement>('[aria-label="View output 1: Natural Black · Completed"]')!.click());
    expect(modal.querySelector(".rrugc-photo-gallery-counter")?.textContent).toBe("01 / 12");
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
    await act(async()=>host.querySelectorAll<HTMLButtonElement>('[data-skill="colorway"] .rrugc-colorway-slot')[1].click());
    modal=document.querySelector<HTMLElement>('[role="dialog"][data-photo-gallery="multi"]')!;
    await act(async()=>modal.querySelector<HTMLButtonElement>(".rrugc-photo-gallery-actions button")!.click());
    expect(retry).toHaveBeenCalledWith(colorJobs[1]);
    await act(async()=>root.unmount());
  });
  it("View all outputs opens 22 ordered positions even when no image jobs exist",async()=>{
    const {root,host}=await renderWorkflow();
    const labels=[...host.querySelectorAll<HTMLButtonElement>(".rrugc-products-photo-footer-actions button")].map(button=>button.textContent?.trim());
    expect(labels).toEqual(["View all outputs","Run all"]);
    expect(host.textContent).not.toContain("7 skill slots");
    expect(host.textContent).not.toContain("22 outputs");
    const trigger=host.querySelector<HTMLButtonElement>('[aria-label="View all outputs"]')!;
    await act(async()=>trigger.click());
    const modal=document.querySelector<HTMLElement>('[role="dialog"][data-photo-gallery="multi"]')!;
    expect(modal).not.toBeNull();
    expect(modal.querySelectorAll(".rrugc-photo-gallery-thumb")).toHaveLength(22);
    expect(modal.textContent).toContain("0 of 22 images generated");
    expect(modal.querySelectorAll('.rrugc-photo-gallery-thumb')[0].textContent).toContain("Front High");
    expect(modal.querySelectorAll('.rrugc-photo-gallery-thumb')[4].textContent).toContain("Multi-hat 1");
    expect(modal.querySelectorAll('.rrugc-photo-gallery-thumb')[7].textContent).toContain("UGC 1");
    expect(modal.querySelectorAll('.rrugc-photo-gallery-thumb')[10].textContent).toContain("Natural Black");
    await act(async()=>root.unmount());
  });

  it("Run all starts one server-owned seven-slot sequence and never queues downstream slots in the browser",async()=>{
    vi.mocked(runAllEcommerceJobs).mockResolvedValueOnce({
      run_id:"run-1",source_plan_id:"design-1",source_revision:"rev-1",
      color_key:"natural-black",status:"running",current_slot:1,error_code:null,error_message:null,
      steps:["Ecommerce 1","Ecommerce 2","Ecommerce 3","Ecommerce 4","Multi-hat","UGC","12 Hat Colors"].map(
        (name,i)=>({stage:i<4?"prod_s2_ec"+(i+1):i===6?"prod_s2_col":"prod_s2_mh_1",
          slot:i+1,name,status:i===0?"running" as const:"pending" as const,
          completed_outputs:0,total_outputs:i===6?12:i>=4?3:1})),
    });
    const queueColors=vi.fn(async()=>({queued:11,existing:2,retried:0}));
    const refresh=vi.fn(async()=>undefined);
    const {root,host}=await renderWorkflow({
      sourcePlanId:"design-1",colors:colorJobs,
      colorwayReady:true,onQueueColors:queueColors,onEcommerceJobChange:refresh,
    });
    const button=host.querySelector<HTMLButtonElement>('[aria-label="Run all"]')!;
    await act(async()=>{button.click();await Promise.resolve();});
    expect(runAllEcommerceJobs).toHaveBeenCalledWith("design-1", "natural-black", undefined);
    expect(queueColors).not.toHaveBeenCalled();
    expect(refresh).toHaveBeenCalledTimes(1);
    const report=host.querySelector<HTMLElement>(".rrugc-products-photo-run-all-report")!;
    expect(report.textContent).toContain("Slot 1/7");
    expect(report.querySelectorAll("li")).toHaveLength(7);
    expect(report.textContent).toContain("12 Hat Colors");
    expect(button.disabled).toBe(true);
    await act(async()=>root.unmount());
  });

  it("Run all reports API failures without attempting any other slot",async()=>{
    vi.mocked(runAllEcommerceJobs).mockRejectedValueOnce(new Error("Insufficient processing credits"));
    const queueColors=vi.fn(async()=>({queued:11,existing:2,retried:0}));
    const {root,host}=await renderWorkflow({
      sourcePlanId:"design-1",colorwayReady:false,onQueueColors:queueColors,
    });
    await act(async()=>{host.querySelector<HTMLButtonElement>('[aria-label="Run all"]')!.click();await Promise.resolve();});
    const report=host.querySelector<HTMLElement>(".rrugc-products-photo-run-all-report")!;
    expect(report.textContent).toContain("Insufficient processing credits");
    expect(queueColors).not.toHaveBeenCalled();
    expect(report.getAttribute("role")).toBe("alert");
    await act(async()=>root.unmount());
  });

  it("loads compact Ecommerce and colorway tiles but original bytes only after opening the modal", async () => {
    const fullEcommerce = "/api/v1/realistic-review-ugc/products-stage2/ecommerce/jobs/one/output";
    const fullColor = "/api/v1/realistic-review-ugc/colorways/one/output";
    const {root, host} = await renderWorkflow({
      ecommerceJobs: [{...ecommerce[0], output_url: fullEcommerce}],
      colors: [{...colorJobs[0], output_url: fullColor}],
    });
    const shot = host.querySelector<HTMLImageElement>('[data-skill="prod_s2_ec4"] .rrugc-products-photo-output-strip img')!;
    const color = host.querySelector<HTMLImageElement>('[data-skill="colorway"] .rrugc-colorway-slot img')!;
    expect(shot.getAttribute("src")).toBe(fullEcommerce + "?thumbnail=true&size=128");
    expect(color.getAttribute("src")).toBe(fullColor + "?thumbnail=true&size=128");
    expect([...host.querySelectorAll("img")].some(img => img.getAttribute("src") === fullEcommerce)).toBe(false);
    await act(async()=>host.querySelector<HTMLButtonElement>('[aria-label="Open Back High Ecommerce output"]')!.click());
    expect(document.querySelector<HTMLImageElement>(".rrugc-photo-gallery-stage img")?.getAttribute("src")).toBe(fullEcommerce);
    await act(async()=>root.unmount());
  });
  it("opens all 22 outputs at the clicked Ecommerce Back High image",async()=>{
    const {root,host}=await renderWorkflow({ecommerceJobs:ecommerce});
    await act(async()=>host.querySelector<HTMLButtonElement>('[aria-label="Open Back High Ecommerce output"]')!.click());
    const modal=document.querySelector<HTMLElement>('[role="dialog"][data-photo-gallery="multi"]')!;
    expect(modal).not.toBeNull();
    expect(modal.textContent).toContain("All outputs · embroidery_design.png");
    expect(modal.textContent).toContain("04 / 22");
    expect(modal.textContent).toContain("All 22 outputs");
    expect(modal.querySelector<HTMLImageElement>(".rrugc-photo-gallery-stage img")?.getAttribute("src")).toBe("/photos/back.png");
    expect(modal.querySelectorAll(".rrugc-photo-gallery-thumbnails button")).toHaveLength(22);
    await act(async()=>root.unmount());
  });
});