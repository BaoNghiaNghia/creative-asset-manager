import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const layoutCssUrl = new URL("../app/realistic-review-ugc/ui-overhaul.css", import.meta.url);
const stage3CssUrl = new URL("../app/realistic-review-ugc/Stage3ReviewGroups.css", import.meta.url);

test("Stage 3 review groups span the full stage grid", async () => {
  const [layoutCss, stage3Css] = await Promise.all([
    readFile(layoutCssUrl, "utf8"),
    readFile(stage3CssUrl, "utf8"),
  ]);

  assert.match(
    layoutCss,
    /\.rrugc-stage-panel>\.rrugc-stage3\{grid-column:1\/-1\}/,
  );
  assert.match(
    stage3Css,
    /\.rrugc-stage3\{display:flex;width:100%;min-width:0;/,
  );
});
