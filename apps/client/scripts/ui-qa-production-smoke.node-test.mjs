import assert from "node:assert/strict";
import test from "node:test";

import { runAssertion, runState } from "./production-ui-smoke.mjs";

function fakePage({ ratio = 1, overflow = 0 } = {}) {
  const calls = { clicks: [], waits: [], screenshots: [] };
  return {
    calls,
    locator(selector) {
      return {
        first() {
          return {
            async count() {
              return 1;
            },
            async click() {
              calls.clicks.push(selector);
            },
            async waitFor() {
              calls.waits.push(selector);
            },
            async focus() {},
            async hover() {},
            async evaluate() {
              return { clientWidth: 100, scrollWidth: 100 + overflow };
            },
          };
        },
      };
    },
    async evaluate() {
      return {
        childWidth: 100 * ratio,
        parentWidth: 100,
        ratio,
      };
    },
    async waitForTimeout() {},
    async screenshot(options) {
      calls.screenshots.push(options.path);
    },
  };
}

test("Production smoke state can click a read-only tab and enforce layout assertions", async () => {
  const page = fakePage();
  const issues = [];

  const filename = await runState(
    page,
    {
      name: "stage3-review",
      click: "#rrugc-tab-stage3",
      waitFor: ".rrugc-stage3",
      assertions: [
        { type: "visible", selector: ".rrugc-stage3" },
        {
          type: "width-ratio",
          selector: ".rrugc-stage3",
          relativeTo: "#rrugc-panel-stage3",
          min: 0.98,
        },
        { type: "no-horizontal-overflow", selector: ".rrugc-stage3" },
      ],
    },
    "realistic-review-ugc",
    "desktop",
    ".",
    issues,
  );

  assert.deepEqual(page.calls.clicks, ["#rrugc-tab-stage3"]);
  assert.ok(page.calls.waits.includes(".rrugc-stage3"));
  assert.equal(page.calls.screenshots.length, 1);
  assert.equal(filename, "desktop--realistic-review-ugc--stage3-review.png");
  assert.deepEqual(issues, []);
});

test("Production smoke records a failed width assertion as a state issue", async () => {
  const page = fakePage({ ratio: 0.5 });
  const issues = [];

  await runState(
    page,
    {
      name: "stage3-review",
      assertions: [
        {
          type: "width-ratio",
          selector: ".rrugc-stage3",
          relativeTo: "#rrugc-panel-stage3",
          min: 0.98,
        },
      ],
    },
    "realistic-review-ugc",
    "desktop",
    ".",
    issues,
  );

  assert.equal(issues.length, 1);
  assert.equal(issues[0].kind, "state");
  assert.match(issues[0].message, /width-ratio failed/);
});

test("Production smoke rejects horizontal overflow beyond tolerance", async () => {
  const page = fakePage({ overflow: 4 });

  await assert.rejects(
    runAssertion(
      page,
      {
        type: "no-horizontal-overflow",
        selector: ".rrugc-stage3-gallery",
        tolerancePx: 1,
      },
      5_000,
    ),
    /horizontal overflow/,
  );
});

test("Production AI Operations viewport check passes with a responsive layout", async () => {
  const page = fakePage();
  page.evaluate = async () => 0;
  const issues = [];
  await runState(
    page,
    {
      name: "default",
      assertions: [
        { type: "visible", selector: ".ops-tabs-carousel" },
        { type: "no-viewport-overflow", selector: "body" },
      ],
    },
    "ai-operations",
    "mobile",
    ".",
    issues,
  );
  assert.deepEqual(issues, []);
});

test("Production AI Operations viewport check detects horizontal page overflow", async () => {
  const page = fakePage();
  page.evaluate = async () => 12;
  await assert.rejects(
    runAssertion(
      page,
      { type: "no-viewport-overflow", selector: "body", tolerancePx: 1 },
      5_000,
    ),
    /viewport overflow: 12px > 1px/,
  );
});
