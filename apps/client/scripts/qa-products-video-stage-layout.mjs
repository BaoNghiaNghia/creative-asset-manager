/**
 * Isolated real-browser layout QA for the Products Stage 3 UI.
 * Requires no production authentication or Seedance credits.
 * Run from apps/client: node scripts/qa-products-video-stage-layout.mjs
 */
import fs from "node:fs";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";
import { chromium } from "playwright";

const server = await createServer({ root: process.cwd(), server: { middlewareMode: true }, appType: "custom", logLevel: "error" });
let browser;
try {
  const { ProductsVideoStage } = await server.ssrLoadModule("/app/realistic-review-ugc/ProductsVideoStage.tsx");
  const markup = renderToStaticMarkup(React.createElement(ProductsVideoStage, { active: false }));
  const css = ["app/realistic-review-ugc/ui-overhaul.css", "app/realistic-review-ugc/ProductsVideoStage.css"]
    .map(file => fs.readFileSync(file, "utf8")).join("\n");
  browser = await chromium.launch({ headless: true, executablePath: process.env.CAM_QA_CHROMIUM || "/usr/bin/google-chrome", args: ["--no-sandbox"] });
  for (const width of [1440, 1024, 768, 390, 320]) {
    const page = await browser.newPage({ viewport: { width, height: 900 }, deviceScaleFactor: 1 });
    await page.setContent(`<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><style>
      body{margin:0;font-family:Arial,sans-serif;background:#f3f6fa}
      .rrugc-page-body{padding:16px;box-sizing:border-box}
      </style><style>${css}</style></head><body>
      <main class="rrugc-page-body"><section class="rrugc-stage-panel">${markup}</section></main>
      </body></html>`);
    const result = await page.evaluate(() => {
      const element = selector => document.querySelector(selector);
      const rect = selector => {
        const { x, width } = element(selector).getBoundingClientRect();
        return [Math.round(x), Math.round(width)];
      };
      return {
        viewport: innerWidth,
        gridColumn: getComputedStyle(element(".rrugc-products-video-stage")).gridColumn,
        stage: rect(".rrugc-products-video-stage"),
        metrics: rect(".rrugc-video-stage-metrics"),
        library: rect(".rrugc-video-stage-library"),
        director: rect(".rrugc-video-stage-director"),
        overflow: document.documentElement.scrollWidth > innerWidth,
      };
    });
    console.log(JSON.stringify(result));
    if (result.stage[1] < width * 0.65 || result.overflow) {
      throw new Error("Products Stage 3 collapsed or overflowed at viewport " + width);
    }
    if (width <= 1180 && result.library[0] !== result.director[0]) {
      throw new Error("Products Stage 3 panels should stack at viewport " + width);
    }
    await page.close();
  }
} finally {
  await browser?.close();
  await server.close();
}
