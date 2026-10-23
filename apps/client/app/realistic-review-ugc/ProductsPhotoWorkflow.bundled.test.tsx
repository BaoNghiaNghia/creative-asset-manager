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
      const runButton = row.querySelector<HTMLButtonElement>(`button[aria-label="Run ${spec.title}"]`)!;
      expect(runButton.disabled).toBe(true);
      expect(runButton.getAttribute("title")).toContain("assigned Skill is unavailable");
      expect(row.querySelector(".rrugc-products-photo-stage-detail")).toBeNull();
      await act(async () => row.querySelector<HTMLButtonElement>(`button[aria-label="${spec.title} details"]`)!.click());
      const panel = row.querySelector<HTMLElement>(".rrugc-products-photo-stage-detail")!;
      expect(panel).not.toBeNull();
      expect(panel.classList.contains("is-bundled-skill-detail")).toBe(true);
      expect(panel.classList.contains("is-ecommerce-detail")).toBe(false);
      expect(panel.querySelector(".rrugc-products-photo-inputs")).toBeNull();
      expect(panel.textContent).toContain("assigned Skill is unavailable or invalid");
      expect(panel.textContent).toContain(spec.references);
      expect(panel.querySelector("details summary")?.textContent).toContain("Missing package files");
      for (const file of spec.missing) expect(panel.textContent).toContain(file);
      expect(panel.querySelector<HTMLButtonElement>(".rrugc-products-photo-detail-actions button")?.textContent).toBe("Upload Skill ZIP");
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
      expect(panel.textContent).toContain("is installed but the worker is unavailable");
      expect(panel.textContent).toContain("worker is unavailable");
      expect(panel.querySelector("details")).toBeNull();
      expect(panel.querySelector<HTMLButtonElement>(".rrugc-products-photo-detail-actions button")?.textContent).toBe("Manage skill");
      expect(row.querySelector<HTMLButtonElement>(`button[aria-label="Run ${spec.title}"]`)?.disabled).toBe(true);
      await act(async () => root.unmount());
    });
  }

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
    const run = row.querySelector<HTMLButtonElement>('[aria-label="Run UGC"]')!;
    expect(run.disabled).toBe(true);
    expect(run.getAttribute("title")).toContain("worker is unavailable");
    await act(async () => row.querySelector<HTMLButtonElement>('[aria-label="UGC details"]')!.click());
    const panel = row.querySelector<HTMLElement>(".is-bundled-skill-detail")!;
    expect(panel.textContent).toContain("Skill ugc-gatorhats is installed but the worker is unavailable");
    expect(panel.textContent).toContain("worker is unavailable");
    expect(panel.textContent).not.toContain("exact approved Skill ZIP");
    expect(panel.querySelector(".rrugc-products-photo-bundle-missing")).toBeNull();
    expect(panel.querySelector<HTMLButtonElement>(".rrugc-products-photo-detail-actions button")?.textContent)
      .toBe("Manage skill");
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