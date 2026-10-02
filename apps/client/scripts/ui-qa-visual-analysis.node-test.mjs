import assert from "node:assert/strict";
import test from "node:test";
import {
  clusterChangedRegions,
  createVisualAnalysis,
  enrichRegions,
  issueSearchTokens,
  paintRegionBoxes,
  renderVisualAnalysisMarkdown,
} from "./ui-qa-visual-analysis.mjs";

function markRect(mask, width, x, y, rectWidth, rectHeight) {
  for (let row = y; row < y + rectHeight; row += 1) {
    for (let column = x; column < x + rectWidth; column += 1) {
      mask[row * width + column] = 1;
    }
  }
}

test("clusters changed pixels into separate actionable regions", () => {
  const width = 100;
  const height = 80;
  const mask = new Uint8Array(width * height);
  markRect(mask, width, 8, 10, 12, 10);
  markRect(mask, width, 72, 52, 14, 11);

  const regions = clusterChangedRegions(mask, width, height, {
    tileSize: 5,
    joinRadiusTiles: 1,
    minPixels: 4,
    padding: 2,
  });

  assert.equal(regions.length, 2);
  assert.equal(regions[0].changedPixels, 154);
  assert.equal(regions[0].zone, "main");
  assert.equal(regions[1].changedPixels, 120);
  assert.ok(regions.every((region) => region.width > 0 && region.height > 0));
});

test("ranks likely DOM elements and state actions for a diff region", () => {
  const regions = [
    {
      id: 1,
      x: 20,
      y: 20,
      width: 40,
      height: 30,
      changedPixels: 300,
      zone: "main",
    },
  ];
  const elements = [
    {
      selector: "main.workspace",
      tag: "main",
      classes: ["workspace"],
      rect: { x: 0, y: 0, width: 200, height: 160 },
    },
    {
      selector: "article.media-card",
      tag: "article",
      classes: ["media-card"],
      rect: { x: 18, y: 18, width: 46, height: 36 },
    },
  ];
  const actions = [
    {
      action: "hover",
      selector: ".grid article:nth-child(2)",
      rect: { x: 18, y: 18, width: 46, height: 36 },
    },
  ];

  const [region] = enrichRegions(regions, elements, actions, 200, 160);

  assert.equal(region.likelyElements[0].selector, "article.media-card");
  assert.equal(region.actionTargets[0].action, "hover");
  assert.equal(region.actionTargets[0].selector, ".grid article:nth-child(2)");
});

test("creates machine-readable and human-readable regression diagnostics", () => {
  const report = {
    runId: "qa-run",
    results: [
      {
        viewport: "tabletPortrait",
        size: { width: 768, height: 1024 },
        visualComparisons: [
          {
            state: "hover-card",
            screenshot: "tabletPortrait--hover-card.png",
            status: "mismatch",
            mismatchedPixels: 420,
            diffRatio: 0.002,
            actualSize: { width: 768, height: 1024 },
            baselineSize: { width: 768, height: 1024 },
            baseline: "visual-baselines/explorer-viewer/tabletPortrait--hover-card.png",
            diff: ".ui-qa/run/diffs/tabletPortrait--hover-card.png",
            diagnostic: ".ui-qa/run/diagnostics/tabletPortrait--hover-card.png",
            actions: [{ action: "hover", selector: ".grid article:nth-child(2)" }],
            sourceHints: [
              {
                path: "apps/client/styles/global.css",
                score: 12,
                matches: ["grid", "media-card"],
              },
            ],
            regions: [
              {
                id: 1,
                x: 100,
                y: 200,
                width: 220,
                height: 180,
                changedPixels: 420,
                zone: "main",
                actionTargets: [
                  {
                    action: "hover",
                    selector: ".grid article:nth-child(2)",
                    overlapRatio: 0.8,
                  },
                ],
                likelyElements: [
                  {
                    selector: "article.media-card",
                    tag: "article",
                    classes: ["media-card"],
                    score: 0.91,
                  },
                ],
              },
            ],
          },
        ],
      },
    ],
  };

  const analysis = createVisualAnalysis(report);
  const markdown = renderVisualAnalysisMarkdown(analysis);

  assert.equal(analysis.status, "failed");
  assert.equal(analysis.issueCount, 1);
  assert.deepEqual(analysis.issues[0].likelySelectors, ["article.media-card"]);
  assert.match(markdown, /tabletPortrait \/ hover-card/);
  assert.match(markdown, /article\.media-card/);
  assert.match(markdown, /apps\/client\/styles\/global\.css/);
  assert.match(markdown, /Do not refresh baselines/);

  assert.deepEqual(issueSearchTokens(analysis.issues[0]).sort(), [
    "grid",
    "media-card",
  ]);
});

test("paints visible boxes around analyzed regions", () => {
  const width = 20;
  const height = 20;
  const rgba = new Uint8Array(width * height * 4);
  paintRegionBoxes(
    rgba,
    width,
    height,
    [{ x: 4, y: 5, width: 10, height: 8 }],
    { thickness: 1, color: [1, 2, 3, 255] },
  );

  const topLeft = (5 * width + 4) * 4;
  const center = (8 * width + 8) * 4;
  assert.deepEqual(Array.from(rgba.slice(topLeft, topLeft + 4)), [1, 2, 3, 255]);
  assert.deepEqual(Array.from(rgba.slice(center, center + 4)), [0, 0, 0, 0]);
});
