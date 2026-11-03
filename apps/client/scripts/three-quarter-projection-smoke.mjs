/* Browser regression for the real 3/4 Canvas warp, ink trimming and mask. */
import { build } from "esbuild";
import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";

const bundle = await build({
  entryPoints: ["app/realistic-review-ugc/ThreeQuarterProjection.ts"],
  bundle: true, platform: "browser", format: "iife", globalName: "camProjection", write: false,
});
const browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
try {
  const page = await browser.newPage({ viewport: { width: 1050, height: 1050 } });
  await page.setContent('<style>body{margin:0;background:#fff}canvas{display:block}</style><canvas id="output" width="1000" height="1000"></canvas>');
  await page.addScriptTag({ content: bundle.outputFiles[0].text });
  const result = await page.evaluate(() => {
    const source = document.createElement("canvas");
    source.width = 1000; source.height = 1000;
    const actx = source.getContext("2d");
    actx.fillStyle = "#dc3620";
    actx.fillRect(200, 348, 600, 300); // lots of transparent source padding
    actx.fillStyle = "#ffd718";
    actx.font = "bold 124px Arial";
    actx.textAlign = "center";
    actx.fillText("HAPPIEST", 500, 540);
    const bounds = camProjection.visibleArtworkBounds(source);
    const canvas = document.querySelector("#output");
    const start = performance.now();
    camProjection.renderCrownProjection(canvas, source, {
      corners: camProjection.CROWN_FIT_CORNERS_8869,
      front: {left: 0.152, top: 0.309, width: 0.696, height: 0.231, upper_line: 0.19, lower_line: 0.63, revision: 1},
      designSize: 100,
    });
    const duration = performance.now() - start;
    const data = canvas.getContext("2d", { willReadFrequently: true }).getImageData(0, 0, 1000, 1000).data;
    let painted = 0, spill = 0, x0 = 1000, x1 = -1, y0 = 1000, y1 = -1;
    const poly = camProjection.CROWN_FIT_CORNERS_8869;
    const insideQuad = (p) => {
      let inside = false;
      for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
        const [xi, yi] = poly[i], [xj, yj] = poly[j];
        if ((yi > p[1]) !== (yj > p[1]) &&
            p[0] < (xj - xi) * (p[1] - yi) / (yj - yi) + xi) inside = !inside;
      }
      return inside;
    };
    for (let y = 0; y < 1000; y++) for (let x = 0; x < 1000; x++) {
      const alpha = data[(y * 1000 + x) * 4 + 3];
      if (alpha < 30) continue;
      painted++; x0 = Math.min(x0, x); x1 = Math.max(x1, x);
      y0 = Math.min(y0, y); y1 = Math.max(y1, y);
      // Allow one antialiased pixel on polygon/mask boundary.
      if (!insideQuad([(x + .5) / 1000, (y + .5) / 1000]) ||
          !camProjection.insideCrown([(x + .5) / 1000, (y + .5) / 1000])) spill++;
    }
    return {bounds, painted, spill, x0, x1, y0, y1, duration};
  });
  await mkdir(".ui-qa", { recursive: true });
  await page.screenshot({ path: ".ui-qa/three-quarter-projection-smoke.png" });
  console.log(JSON.stringify(result));
  if (result.bounds.width > 660 || result.bounds.width < 580 ||
      result.painted < 28000 || result.x1 - result.x0 < 260 ||
      result.y1 - result.y0 < 110 || result.spill > 5 ||
      result.duration > 2500) process.exitCode = 1;
  await page.close();
} finally { await browser.close(); }
