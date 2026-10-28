// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";
import { ProductsPhotoWorkflow } from "./ProductsPhotoWorkflow";
import type { ProductsStage2PhotoReadiness } from "./api";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
afterEach(() => document.body.replaceChildren());

const bundles = [
  {
    key: "prod_s2_mh", readinessKey: "multihat" as const, title: "Multi-hat",
    name: "gatorhats-8869-multihat-v2-4",
    missing: ["SKILL.md", "references/STOCK_AND_QA.md", "references/THREAD_MAP.md", "scripts/preflight_stock.py"],
    references: "Exact embroidery, verified real stock ZIP and three scene references",
  },
  {
    key: "prod_s2_ugc", readinessKey: "ugc" as const, title: "UGC",
    name: "gatorhats-8869-ugc-v3",
    missing: ["SKILL.md", "references/PRODUCT_LOCK.md", "assets/valucap_8869_stock_images.zip", "scripts/qa_visual_gate.py"],
    references: "Current scene/model references, verified stock and visual five-panel QA",
  },
];

function readiness(spec: typeof bundles[number], installed: boolean): ProductsStage2PhotoReadiness {
  return {
    workflow: "products_stage2_photo_pipeline",
    sequential: true,
    ready: false,
    total_outputs_per_design: 10,
    steps: [{
      key: spec.readinessKey,
      skill_name: spec.name,
      title: spec.title,
      output_count: 3,
      shot_labels: ["one", "two", "three"],
      ready: false,
      bundle_installed: installed,
      status: installed ? "needs_pipeline_worker" : "needs_install",
      missing_assets: installed ? [] : spec.missing,
      required_inputs: spec.references,
    }],
  };
}

describe("Product Stage 2 Skills 5 and 6 setup/details", () => {
  for (const spec of bundles) {
    it(`${spec.title}: opens a compact Skill setup panel and never pretends the disabled worker can run`, async () => {
      const host = document.createElement("div");
      document.body.append(host);
      const root = createRoot(host);
      await act(async () => root.render(<ProductsPhotoWorkflow
        sourceName="Embroidery.png" readiness={readiness(spec, false)}
        configuredSkillNames={{ [spec.key]: spec.name }}
      />));
      const row = host.querySelector<HTMLElement>(`[data-skill="${spec.key}"]`)!;
      const blockedState = row.querySelector<HTMLElement>(".rrugc-products-photo-run-state")!;
      expect(blockedState.className).toContain("is-blocked");
      expect(blockedState.getAttribute("title")).toContain("assigned Skill is unavailable");
      expect(row.querySelector(`button[aria-label="Run ${spec.title}"]`)).toBeNull();
      expect(row.querySelector(".rrugc-products-photo-stage-detail")).toBeNull();
      await act(async () => row.querySelector<HTMLButtonElement>(`button[aria-label="${spec.title} details"]`)!.click());
      const panel = row.querySelector<HTMLElement>(".rrugc-products-photo-stage-detail")!;
      expect(panel).not.toBeNull();
      expect(panel.classList.contains("is-bundled-skill-detail")).toBe(true);
      expect(panel.classList.contains("is-ecommerce-detail")).toBe(false);
      expect(panel.querySelector(".rrugc-products-photo-inputs")).toBeNull();
      expect(panel.querySelector(".rrugc-products-photo-detail-info")).toBeNull();
      expect(panel.textContent).not.toContain(spec.references);
      expect(panel.querySelector("details summary")).toBeNull();
      for (const file of spec.missing) expect(panel.textContent).not.toContain(file);
      expect(panel.querySelector<HTMLButtonElement>(".rrugc-products-photo-detail-actions button")).toBeNull();
      expect(panel.textContent).toContain("Worker or Skill must be ready before running.");
      expect(panel.textContent).not.toContain("Logs available after the first job");
      await act(async () => row.querySelector<HTMLButtonElement>(`button[aria-label="${spec.title} details"]`)!.click());
      expect(row.querySelector(".rrugc-products-photo-stage-detail")).toBeNull();
      await act(async () => root.unmount());
    });

    it(`${spec.title}: distinguishes installed bundle from unavailable worker`, async () => {
      const host = document.createElement("div");
      document.body.append(host);
      const root = createRoot(host);
      await act(async () => root.render(<ProductsPhotoWorkflow
        sourceName="Embroidery.png" readiness={readiness(spec, true)}
        configuredSkillNames={{ [spec.key]: spec.name }}
      />));
      const row = host.querySelector<HTMLElement>(`[data-skill="${spec.key}"]`)!;
      await act(async () => row.querySelector<HTMLButtonElement>(`button[aria-label="${spec.title} details"]`)!.click());
      const panel = row.querySelector<HTMLElement>(".is-bundled-skill-detail")!;
      expect(panel.textContent).not.toContain("is installed but the worker is unavailable");
      expect(row.querySelector<HTMLElement>(".rrugc-products-photo-run-state")?.getAttribute("title")).toContain("worker is unavailable");
      expect(panel.querySelector("details")).toBeNull();
      expect(panel.querySelector<HTMLButtonElement>(".rrugc-products-photo-detail-actions button")).toBeNull();
      expect(row.querySelector<HTMLButtonElement>(`button[aria-label="Run ${spec.title}"]`)).toBeNull();
      await act(async () => root.unmount());
    });
  }

  it("shows the per-design pinned 3-hat and 4-hat references in Slot 5 details", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<ProductsPhotoWorkflow
      sourceName="design-one.png"
      sourcePlanId="design-one"
      readiness={readiness(bundles[0], true)}
      configuredSkillNames={{ prod_s2_mh: bundles[0].name }}
      ecommerceJobs={[{
        id: "multi-job-1", source_plan_id: "design-one", stage: "prod_s2_mh_1",
        status: "queued", retry_count: 0, attempt_count: 0, error_code: null,
        output_url: null, skill_name: bundles[0].name,
        scene_colorways: [
          { key: "natural-red", label: "Natural/Red" },
          { key: "natural-royal", label: "Natural/Royal" },
          { key: "natural-brown", label: "Natural/Brown" },
          { key: "natural-black", label: "Natural/Black" },
        ],
        scene_references: [
          { image_id: "scene-3", image_url: "https://i.pinimg.com/736x/3.jpg",
            pin_url: "https://www.pinterest.com/pin/123/", caps_count: 3 },
          { image_id: "scene-4", image_url: "https://i.pinimg.com/736x/4.jpg",
            pin_url: "https://www.pinterest.com/pin/124/", caps_count: 4 },
        ],
      }]}
    />));
    const row = host.querySelector<HTMLElement>('[data-skill="prod_s2_mh"]')!;
    await act(async () => row.querySelector<HTMLButtonElement>('button[aria-label="Multi-hat details"]')!.click());
    const preview = row.querySelector<HTMLElement>('[aria-label="Selected Multi-hat photos"]')!;
    expect(preview.querySelectorAll("a").length).toBe(2);
    expect(preview.textContent).toContain("3 hats");
    expect(preview.textContent).toContain("4 hats");
    expect(preview.querySelector("img")?.getAttribute("src")).toBe("https://i.pinimg.com/236x/3.jpg");
    expect(row.textContent).toContain("2/20 references");
    expect(row.querySelector('[aria-label="Approved Multi-hat colors by output"]')).toBeNull();
    expect(row.querySelector(".rrugc-multihat-output-palettes")).toBeNull();
    expect(row.textContent).not.toContain("Official 8869 cap colors");
    expect(row.textContent).not.toContain("Natural/Red");
    await act(async () => root.unmount());
  });

  it("hides the previous-output regenerate panel for completed Multi-hat shots", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<ProductsPhotoWorkflow
      sourceName="design.png" sourcePlanId="design-1"
      readiness={readiness(bundles[0], true)}
      configuredSkillNames={{ prod_s2_mh: bundles[0].name }}
      ecommerceJobs={[{
        id: "old-mh", source_plan_id: "design-1", stage: "prod_s2_mh_1",
        status: "completed", retry_count: 0, attempt_count: 1, error_code: null,
        output_url: "/api/v1/test-output", skill_name: bundles[0].name,
        scene_colorways: [],
      }]}
    />));
    const row = host.querySelector<HTMLElement>('[data-skill="prod_s2_mh"]')!;
    await act(async () => row.querySelector<HTMLButtonElement>('button[aria-label="Multi-hat details"]')!.click());
    expect(row.textContent).not.toContain("previous output");
    expect(row.textContent).not.toContain("Regenerate with different hat colors");
    expect(row.querySelector('[aria-label="Approved Multi-hat colors by output"]')).toBeNull();
    expect(row.querySelector<HTMLButtonElement>('[aria-label="Regenerate prod_s2_mh_1"]')).toBeNull();
    await act(async () => root.unmount());
  });

  it("recognizes uploaded ugc-gatorhats as installed, without enabling an absent worker", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const ugc = bundles[1];
    const assigned = readiness(ugc, true);
    assigned.steps[0].skill_name = "ugc-gatorhats";
    await act(async () => root.render(<ProductsPhotoWorkflow
      sourceName="Embroidery.png" readiness={assigned}
      configuredSkillNames={{ prod_s2_ugc: "ugc-gatorhats" }}
    />));
    const row = host.querySelector<HTMLElement>('[data-skill="prod_s2_ugc"]')!;
    const blockedState = row.querySelector<HTMLElement>(".rrugc-products-photo-run-state")!;
    expect(blockedState.className).toContain("is-blocked");
    expect(row.querySelector('button[aria-label="Run UGC"]')).toBeNull();
    expect(blockedState.getAttribute("title")).toContain("worker is unavailable");
    await act(async () => row.querySelector<HTMLButtonElement>('[aria-label="UGC details"]')!.click());
    const panel = row.querySelector<HTMLElement>(".is-bundled-skill-detail")!;
    expect(panel.textContent).not.toContain("Skill ugc-gatorhats is installed but the worker is unavailable");
    expect(blockedState.getAttribute("title")).toContain("worker is unavailable");
    expect(panel.textContent).not.toContain("exact approved Skill ZIP");
    expect(panel.querySelector(".rrugc-products-photo-bundle-missing")).toBeNull();
    expect(panel.querySelector<HTMLButtonElement>(".rrugc-products-photo-detail-actions button")).toBeNull();
    await act(async () => root.unmount());
  });

  for (const spec of bundles) {
    it(`${spec.title}: enables Run once the installed worker and Skill are ready`, async () => {
      const host = document.createElement("div");
      document.body.append(host);
      const root = createRoot(host);
      const ready = readiness(spec, true);
      ready.steps[0].ready = true;
      ready.steps[0].status = "ready";
      await act(async () => root.render(<ProductsPhotoWorkflow
        sourceName="Embroidery.png" sourcePlanId="source-plan-1"
        readiness={ready} configuredSkillNames={{ [spec.key]: spec.name }}
      />));
      const button = host.querySelector<HTMLButtonElement>(
        `[data-skill="${spec.key}"] button[aria-label="Run ${spec.title}"]`
      );
      expect(button?.disabled).toBe(false);
      await act(async () => root.unmount());
    });
  }

  it("keeps Ecommerce details separate from bundled-skill readiness", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<ProductsPhotoWorkflow sourceName="Embroidery.png" readiness={null} />));
    const row = host.querySelector<HTMLElement>('[data-skill="prod_s2_ec1"]')!;
    await act(async () => row.querySelector<HTMLButtonElement>('button[aria-label="Ecommerce · Image 01 details"]')!.click());
    expect(row.querySelector(".is-ecommerce-detail")).not.toBeNull();
    expect(row.querySelector(".is-bundled-skill-detail")).toBeNull();
    expect(row.querySelector(".rrugc-products-photo-inputs")).not.toBeNull();
    await act(async () => root.unmount());
  });
});