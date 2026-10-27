import { existsSync, readFileSync } from "node:fs";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { chromium } from "playwright";
import { describe, expect, it } from "vitest";
import { ProductsPhotoWorkflow } from "./ProductsPhotoWorkflow";

const stylesheet = [
  "ProductsPhotoWorkflow.css",
  "ProductsPhotoWorkflowTwoColumns.css",
  "ProductsPhotoWorkflowMetaLayout.css",
].map(name => readFileSync(new URL("./" + name, import.meta.url), "utf8")).join("\n");


const browserExecutable = existsSync(chromium.executablePath())
  ? chromium.executablePath()
  : existsSync("/opt/google/chrome/chrome") ? "/opt/google/chrome/chrome" : null;

describe("Stage 2 row alignment in an actual browser", () => {
  (browserExecutable ? it : it.skip)("aligns each card's title directly above Skill, groups timestamps next to its name, and anchors actions before it", async () => {
    const browser = await chromium.launch({ executablePath: browserExecutable, headless: true, args: ["--no-sandbox", "--disable-setuid-sandbox"] });
    try {
      const page = await browser.newPage({ viewport: { width: 1740, height: 1000 }, deviceScaleFactor: 1 });
      const html = renderToStaticMarkup(React.createElement(ProductsPhotoWorkflow, { sourceName: "Sample design.png", readiness: null }));
      await page.setContent('<!doctype html><html><head><meta charset="utf-8"><style>body{margin:0;padding:16px;background:#f5f7fb}' +
        stylesheet + '</style></head><body>' + html + '</body></html>', { waitUntil: "domcontentloaded" });
      const boxes = await page.locator(".rrugc-products-photo-stage-row").evaluateAll(rows => rows.map(node => {
        const el = node;
        const box = (selector) => {
          const element = el.querySelector(selector);
          if (!element) throw new Error("Missing layout target " + selector);
          const r = element.getBoundingClientRect();
          return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width };
        };
        return { colorway: el.classList.contains("is-colorway"), controls: box(".rrugc-products-photo-stage-controls"),
          title: box(".rrugc-products-photo-stage-title"), skill: box(".rrugc-products-photo-skill"),
          name: box(".rrugc-products-photo-skill > strong"),
          date: box(".rrugc-products-photo-updated"), duration: box(".rrugc-products-photo-duration"),
          output: box(".rrugc-products-photo-output-strip") };
      }));
      expect(boxes).toHaveLength(7);
      for (const row of boxes) {
        expect(row.controls.right).toBeLessThan(row.title.left);
        expect(Math.abs(row.title.left - row.skill.left)).toBeLessThan(3);
        expect(row.date.left).toBeGreaterThan(row.name.right);
        expect(row.date.left - row.name.right).toBeLessThan(24);
        expect(row.duration.left).toBeGreaterThan(row.date.right);
        expect(Math.abs(row.skill.top - row.date.top)).toBeLessThan(7);
        expect(row.duration.right).toBeLessThanOrEqual(row.skill.right + 1);
        if (!row.colorway) expect(row.output.left).toBeGreaterThan(row.title.right);
      }
      // Real CSS/container breakpoints: keep controls, titles, timestamps and
      // thumbnails inside each card without horizontal overflow.
      for (const width of [1450, 1020, 620, 390]) {
        await page.setViewportSize({ width, height: 1000 });
        const locations = await page.locator(".rrugc-products-photo-stage-row").evaluateAll(rows => rows.map(node => {
          const card = node.getBoundingClientRect();
          const right = (selector) => {
            const part = node.querySelector(selector);
            return part ? part.getBoundingClientRect().right : -1;
          };
          return {
            rowRight: card.right,
            controls: right(".rrugc-products-photo-stage-controls"),
            title: right(".rrugc-products-photo-stage-title"),
            date: right(".rrugc-products-photo-updated"),
            duration: right(".rrugc-products-photo-duration"),
            output: right(".rrugc-products-photo-output-strip"),
          };
        }));
        expect(locations).toHaveLength(7);
        for (const row of locations) {
          for (const elementRight of [row.controls, row.title, row.date, row.duration, row.output]) {
            expect(elementRight).toBeLessThanOrEqual(row.rowRight + 2);
          }
        }
      }
    } finally { await browser.close(); }
  }, 30000);
});
