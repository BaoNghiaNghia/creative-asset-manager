import { describe, it, expect } from "vitest";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ProductsPhotoWorkflow } from "./ProductsPhotoWorkflow";

describe("Products Stage 2 photo workflow", () => {
  it("shows three sequential skill groups and safely disables incomplete execution", () => {
    const html = renderToStaticMarkup(<ProductsPhotoWorkflow sourceName="design_01.png" readiness={null}/>);
    expect(html).toContain("Ecommerce");
    expect(html).toContain("Multi-hat");
    expect(html).toContain("UGC");
    expect(html).toContain("Three-skill photo workflow for design_01.png");
    expect(html).toContain("Details");
    expect(html).not.toContain("Completed · 10/10");
  });
});
