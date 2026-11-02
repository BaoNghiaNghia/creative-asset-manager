import { describe, it, expect } from "vitest";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ProductsPhotoWorkflow, legacyMissingAssetsForStep } from "./ProductsPhotoWorkflow";
import type { ColorwayJob } from "./api";

describe("Products Stage 2 photo workflow", () => {
  it("places actions first and both time values beside Skill in all seven rows", () => {
    const html = renderToStaticMarkup(<ProductsPhotoWorkflow sourceName="design.png" readiness={null} />);
    const rows = html.split(/<div class="rrugc-products-photo-stage-row(?: is-colorway)?" data-skill="/).slice(1);
    expect(rows).toHaveLength(7);
    for (const row of rows) {
      const controls = row.indexOf('class="rrugc-products-photo-stage-controls"');
      const title = row.indexOf('class="rrugc-products-photo-stage-title"');
      const skill = row.indexOf('class="rrugc-products-photo-skill"');
      const timing = row.indexOf('class="rrugc-products-photo-stage-times"');
      const updated = row.indexOf('class="rrugc-products-photo-updated"', timing);
      const duration = row.indexOf('class="rrugc-products-photo-duration"', timing);
      expect(controls).toBeGreaterThan(-1);
      expect(controls).toBeLessThan(title);
      expect(skill).toBeLessThan(timing);
      expect(updated).toBeGreaterThan(timing);
      expect(duration).toBeGreaterThan(timing);
      expect(updated).toBeLessThan(duration);
    }
  });
  it("does not apply obsolete four-image bundle requirements to independent Ecommerce slots", () => {
    const obsolete = ["SKILL.md", "references/SHOT_VISIBILITY_AND_QA.md", "scripts/release_check.py"];
    expect(legacyMissingAssetsForStep(true, obsolete)).toEqual([]);
    expect(legacyMissingAssetsForStep(false, obsolete)).toEqual(obsolete);
  });
  it("maps the four Ecommerce skills to their own design/hat reference sets without enabling unfinished workers", () => {
    const html = renderToStaticMarkup(<ProductsPhotoWorkflow
      sourceName="design_02.png" designSrc="/source/design-02.png" readiness={null} />);
    for (const label of ["Ecommerce · Image 01", "Ecommerce · Image 02", "Ecommerce · Image 03", "Ecommerce · Image 04"]) {
      expect(html).toContain(label);
    }
    expect(html).not.toContain('class="rrugc-products-photo-inputs"');
    expect(html).toContain('class="rrugc-products-photo-stage-list is-two-column"');
    expect(html).not.toContain(">Last updated:");
    expect(html).not.toContain(">Duration:");
    // Blocked/complete states are labels, not disabled action buttons.
    expect(html).toContain("rrugc-products-photo-run-state is-blocked");
    expect(html).not.toContain("rrugc-products-photo-run-button is-unavailable");
  });

  it("explains when Ecommerce is waiting for an eligible Codex Stage 2 account", () => {
    const html = renderToStaticMarkup(<ProductsPhotoWorkflow
      sourceName="design.png" readiness={null}
      ecommerceJobs={[{
        id: "waiting-codex", source_plan_id: "plan-1", stage: "prod_s2_ec1",
        status: "queued", retry_count: 0, attempt_count: 2,
        error_code: "codex_account_pool_busy", output_url: null,
        skill_name: "cam-slot1-thread-reference-inpaint-v2",
      }]} />);
    expect(html).toContain("Waiting Codex");
    expect(html).toContain("Stage 2 enabled in Codex Account Manager");
    expect(html).toContain("retries automatically without consuming an attempt");
    expect(html).not.toContain("Queued Ecommerce · Image 01");
  });

  it("renders completed Ecommerce photos as clickable filled tiles while leaving missing photos as placeholders", () => {
    const html = renderToStaticMarkup(<ProductsPhotoWorkflow
      sourceName="design.png" readiness={null}
      ecommerceJobs={[{
        id: "hat-01", source_plan_id: "plan-1", stage: "prod_s2_ec1",
        status: "completed", retry_count: 0, attempt_count: 1,
        error_code: null, output_url: "/hat-output.png", skill_name: "ecommerce-01",
      }]} />);
    expect(html).toContain("Open Cap Front · ThreadTransfer output");
    expect(html).toContain("Embroidery Reference");
    expect(html).toContain("rrugc-deferred-img");
    expect((html.match(/rrugc-products-photo-slot1-output is-completed/g) ?? []).length).toBe(1);
    expect(html).toContain('title="Embroidery Reference · Not generated"');
    expect(html).toContain('title="Front Right 3/4 · Not generated"');
  });

  it("shows the persisted Embroidery Reference while Pass B is running", () => {
    const html = renderToStaticMarkup(<ProductsPhotoWorkflow sourceName="Cowboy Hat.png"
      readiness={null} ecommerceJobs={[{
        id: "front-1", source_plan_id: "source", stage: "prod_s2_ec1",
        status: "running", retry_count: 0, attempt_count: 1,
        error_code: null, output_url: null,
        embroidery_reference_url: "/api/ref/embroidery-reference.png",
        skill_name: "cam-slot1-thread-reference-inpaint-v2",
      }]} />);
    expect(html).toContain('aria-label="Open Embroidery Reference output"');
    expect(html).toContain('alt="Embroidery Reference"');
    expect(html).toContain("Cap Front · ThreadTransfer");
    expect(html).toContain('title="Cap Front · ThreadTransfer · Not generated"');
  });

  it("shows both Slot 1 images after completion and retains the reference after retry failure", () => {
    const base = {
      id: "front-2", source_plan_id: "source", stage: "prod_s2_ec1",
      retry_count: 0, attempt_count: 1, error_code: null,
      embroidery_reference_url: "/api/ref/photo.png",
      skill_name: "cam-slot1-thread-reference-inpaint-v2",
    };
    const html = renderToStaticMarkup(<ProductsPhotoWorkflow sourceName="Cowboy Hat.png"
      readiness={null} ecommerceJobs={[{
        ...base, status: "completed", output_url: "/api/front/photo.png",
      }]} />);
    expect(html).toContain('aria-label="Open Embroidery Reference output"');
    expect(html).toContain('aria-label="Open Cap Front · ThreadTransfer output"');
    const failed = renderToStaticMarkup(<ProductsPhotoWorkflow sourceName="Cowboy Hat.png"
      readiness={null} ecommerceJobs={[{
        ...base, status: "failed", output_url: null,
      }]} />);
    expect(failed).toContain('aria-label="Open Embroidery Reference output"');
    expect(failed).toContain("Cap Front · ThreadTransfer · Not generated");
  });

  it("shows seven skill rows, with two Slot 1 outputs and 12 colors", () => {
    const html = renderToStaticMarkup(<ProductsPhotoWorkflow sourceName="design_01.png" readiness={null} />);
    expect(html).toContain("Ecommerce · Image 01");
    expect(html).toContain("Ecommerce · Image 02");
    expect(html).toContain("Ecommerce · Image 03");
    expect(html).toContain("Ecommerce · Image 04");
    expect(html).toContain("Multi-hat");
    expect(html).toContain("UGC");
    expect(html).toContain("12 Hat Colors");
    expect(html).toContain("Seven-slot photo workflow for design_01.png");
    expect(html).not.toContain("<strong>7 skill slots");
    expect(html).not.toContain("<small>Skills 1–4: Ecommerce Images 01–04");
    expect(html).not.toContain("Skills 1–4: Ecommerce Images 01–04");
    expect(html).toContain("View all outputs");
    expect(html).toContain("Run all");
    expect(html).toContain("Download all outputs");
    expect(html.indexOf("View all outputs")).toBeLessThan(html.indexOf("Run all"));
    expect(html.indexOf("Run all")).toBeLessThan(html.indexOf("Download all outputs"));
    expect((html.match(/rrugc-products-photo-stage-row/g) || []).length).toBe(7);
    expect(html).toContain('class="rrugc-products-photo-stage-list is-two-column"');
    expect((html.match(/class="rrugc-products-photo-updated"/g) || []).length).toBe(7);
    expect((html.match(/class="rrugc-products-photo-duration"/g) || []).length).toBe(7);
    expect(html).not.toContain(">Last updated:");
    expect(html).not.toContain(">Duration:");
    expect((html.match(/rrugc-products-photo-empty-shot/g) || []).length).toBe(11);
    for (const slot of ["prod_s2_ec1", "prod_s2_ec2", "prod_s2_ec3", "prod_s2_ec4", "prod_s2_mh", "prod_s2_ugc", "colorway"]) {
      expect(html).toContain('data-skill="' + slot + '"');
    }
    expect(html).toContain("ThreadTransfer V2 · Colorways");
    expect((html.match(/rrugc-products-photo-empty-shot/g) || []).length).toBe(11);
    expect((html.match(/rrugc-colorway-slot is-pending/g) || []).length).toBe(12);
    expect(html).not.toContain("Completed · 10/10");
  });

  it("shows an independent assigned skill and status for every row, with a selectable colorway skill", () => {
    const readiness = {
      workflow: "products_stage2_photo_pipeline",
      sequential: true,
      ready: false,
      total_outputs_per_design: 10,
      steps: [
        { key: "ecommerce" as const, skill_name: "ecommerce-v2", title: "Ecommerce", output_count: 4,
          shot_labels: [], ready: false, bundle_installed: true, status: "needs_pipeline_worker" as const,
          missing_assets: [], required_inputs: "References" },
        { key: "multihat" as const, skill_name: "multihat-v2", title: "Multi-hat", output_count: 3,
          shot_labels: [], ready: false, bundle_installed: false, status: "needs_install" as const,
          missing_assets: ["SKILL.md"], required_inputs: "References" },
        { key: "ugc" as const, skill_name: "ugc-v3", title: "UGC", output_count: 3,
          shot_labels: [], ready: false, bundle_installed: true, status: "needs_pipeline_worker" as const,
          missing_assets: [], required_inputs: "References" },
      ],
    };
    const html = renderToStaticMarkup(<ProductsPhotoWorkflow sourceName="design.png" readiness={readiness}
      colorwaySkillKey="scale" colorwaySkillName="8869 scale"
      colorwaySkillOptions={[
        { value: "scale", label: "8869 scale", ready: true },
        { value: "alternate", label: "Other colorway", ready: false },
      ]}
      onColorwaySkillChange={() => undefined}
      configuredSkillNames={{
        prod_s2_ec1: "ecommerce-image-01",
        prod_s2_ec2: "ecommerce-image-02",
        prod_s2_ec3: "ecommerce-image-03",
        prod_s2_mh: "multihat-v2",
        prod_s2_ugc: "ugc-v3",
      }} />);
    expect(html).toContain("ThreadTransfer V1 · Raster placement");
    expect(html).toContain("ThreadTransfer V1 · Raster placement");
    expect(html).toContain("Method");
    expect(html).not.toContain("ecommerce-image-01");
    expect(html).not.toContain("ecommerce-image-02");
    expect(html).not.toContain("ecommerce-image-03");
    for (const skill of ["multihat-v2", "ugc-v3"]) {
      expect(html).toContain(skill);
    }
    expect(html).toContain("ThreadTransfer V2 · 3/4 projection");
    expect(html).toContain("Built-in · Original Back Stock");
    expect((html.match(/rrugc-products-photo-duration/g) || []).length).toBe(7);
    expect(html).toContain('class="rrugc-products-photo-stage-list is-two-column"');
    expect(html).not.toContain(">Last updated:");
    expect(html).not.toContain(">Duration:");
    expect(html).toContain('title="ThreadTransfer V2 · Colorways"');
    expect(html).toContain('<strong>ThreadTransfer V2 · Colorways</strong>');
    expect(html).not.toContain('aria-label="12 Hat Colors skill for design.png"');
    expect(html).toContain("Not generated · Open all 12 outputs");
  });

  it("renders real completed colorway previews and independent retry actions", () => {
    const jobs: ColorwayJob[] = [
      {
        id: "one", source_plan_id: "src", color_key: "natural-brown",
        color_name: "Natural Brown", status: "completed", retry_count: 0,
        attempt_count: 1, error_code: null, output_url: "/real-photo.png",
      },
      {
        id: "two", source_plan_id: "src", color_key: "natural-black",
        color_name: "Natural Black", status: "failed", retry_count: 1,
        attempt_count: 2, error_code: "skill_failed", output_url: null,
      },
    ];
    const html = renderToStaticMarkup(<ProductsPhotoWorkflow sourceName="design_01.png" readiness={null}
      colors={jobs} onOpenColor={() => undefined} onRetryColor={() => undefined} />);
    expect(html).toContain("rrugc-deferred-img");
    expect(html).toContain("View Natural Brown in 12 hat colors gallery");
    expect(html).toContain("View Natural Black in 12 hat colors gallery");
    expect(html).toContain('aria-haspopup="dialog"');
    expect((html.match(/rrugc-colorway-slot is-pending/g) || []).length).toBe(10);
    expect((html.match(/aria-haspopup="dialog"/g) || []).length).toBe(12);
  });
});