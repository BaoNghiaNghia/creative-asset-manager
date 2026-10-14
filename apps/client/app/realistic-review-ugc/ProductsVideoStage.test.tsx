import React from "react";
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ProductsVideoStage } from "./ProductsVideoStage";

describe("GatorHats Products Stage 3 Seedance workspace", () => {
  it("shows a distinct Products video stage with empty source state and safe generation gate", () => {
    const html = renderToStaticMarkup(<ProductsVideoStage active={false} />);
    expect(html).toContain("Stage 2 photos → Seedance 2.5 videos");
    expect(html).toContain("Stage 2 image library");
    expect(html).toContain("Video director");
    expect(html).toContain("Studio turntable");
    expect(html).toContain("Embroidery macro");
    expect(html).toContain("Video history");
    expect(html).toContain("Checking Seedance capability");
    expect(html).toContain('aria-label="Search Stage 2 designs"');
    expect(html).toContain('aria-label="Video aspect ratio"');
    expect(html).toContain('aria-label="Video duration"');
    expect(html).toContain('disabled=""');
    expect(html).not.toContain("video-generation-shell");
  });
});
