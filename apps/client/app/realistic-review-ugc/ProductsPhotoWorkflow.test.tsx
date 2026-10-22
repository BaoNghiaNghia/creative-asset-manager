import { describe, it, expect } from "vitest";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ProductsPhotoWorkflow } from "./ProductsPhotoWorkflow";
import type { ColorwayJob } from "./api";

describe("Products Stage 2 photo workflow", () => {
  it("maps the four Ecommerce skills to their own design/hat reference sets without enabling unfinished workers", () => {
    const html = renderToStaticMarkup(<ProductsPhotoWorkflow
      sourceName="design_02.png" designSrc="/source/design-02.png" readiness={null} />);
    for (const label of ["Ecommerce · Image 01", "Ecommerce · Image 02", "Ecommerce · Image 03", "Ecommerce · Image 04"]) {
      expect(html).toContain(label);
    }
    expect(html).not.toContain('class="rrugc-products-photo-inputs"');
    expect(html).toContain("Worker pending");
  });

  it("shows seven configurable skill rows with 10 photo placeholders and 13 colors", () => {
    const html = renderToStaticMarkup(<ProductsPhotoWorkflow sourceName="design_01.png" readiness={null} />);
    expect(html).toContain("Ecommerce · Image 01");
    expect(html).toContain("Ecommerce · Image 02");
    expect(html).toContain("Ecommerce · Image 03");
    expect(html).toContain("Ecommerce · Image 04");
    expect(html).toContain("Multi-hat");
    expect(html).toContain("UGC");
    expect(html).toContain("13 Hat Colors");
    expect(html).toContain("Seven-skill photo workflow for design_01.png");
    expect(html).toContain("7 skill slots");
    expect(html).toContain("23 outputs");
    expect((html.match(/rrugc-products-photo-stage-row/g) || []).length).toBe(7);
    expect((html.match(/rrugc-products-photo-empty-shot/g) || []).length).toBe(10);
    for (const slot of ["prod_s2_ec1", "prod_s2_ec2", "prod_s2_ec3", "prod_s2_ec4", "prod_s2_mh", "prod_s2_ugc", "colorway"]) {
      expect(html).toContain('data-skill="' + slot + '"');
    }
    expect(html).toContain("Skill 7");
    expect((html.match(/rrugc-products-photo-empty-shot/g) || []).length).toBe(10);
    expect((html.match(/rrugc-colorway-slot is-pending/g) || []).length).toBe(13);
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
    for (const skill of [
      "ecommerce-image-01", "ecommerce-image-02", "ecommerce-image-03",
      "multihat-v2", "ugc-v3",
    ]) {
      expect(html).toContain(skill);
    }
    expect((html.match(/Worker pending/g) || []).length).toBe(5);
    expect(html).toContain("Needs skill");
    expect(html).toContain('aria-label="13 Hat Colors skill for design.png"');
    expect(html).toContain('<option value="scale" selected="">8869 scale</option>');
    expect(html).toContain("Other colorway · needs sync");
    expect(html).toContain("Not run");
  });

  it("renders real completed colorway previews and independent retry actions", () => {
    const jobs: ColorwayJob[] = [
      {
        id: "one", source_plan_id: "src", color_key: "khaki-maroon",
        color_name: "Khaki Maroon", status: "completed", retry_count: 0,
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
    expect(html).toContain('src="/real-photo.png"');
    expect(html).toContain("View Khaki Maroon output versions");
    expect(html).toContain("Retry Natural Black");
    expect((html.match(/rrugc-colorway-slot is-pending/g) || []).length).toBe(11);
  });
});