import assert from "node:assert/strict";
import test from "node:test";
import { assertUiStep, assertUiState } from "./ui-qa-assertions.mjs";

function fakePage({ text = "Ready for review", count = 2, overflow = 0, visible = true, undersizedControls = [] } = {}) {
  return { evaluate: async () => [], locator(selector) {
    return {
      count: async () => count,
      first() { return this; },
      innerText: async () => text,
      waitFor: async ({ state }) => { if (state === "visible" && !visible) throw Error("not visible"); },
      getAttribute: async name => name === "aria-label" ? "Review" : null,
      isChecked: async () => true,
      evaluate: async () => overflow,
      evaluateAll: async () => undersizedControls,
    };
  } };
}

test("interaction assertions validate text, visibility, attributes and count", async () => {
  const page = fakePage();
  await assertUiStep(page, [
    { type: "visible", selector: "#card" },
    { type: "text-includes", selector: "#card", value: "Ready" },
    { type: "attribute", selector: "#card", name: "aria-label", value: "Review" },
    { type: "count", selector: ".card", value: 2 },
    { type: "checked", selector: "#check", value: true },
    { type: "no-horizontal-overflow", selector: ".table", maxPx: 2 },
    { type: "no-viewport-overflow", selector: "html", maxPx: 0 },
    { type: "min-control-size", selector: ".vote-buttons", minPx: 24 },
    { type: "icon-visible", selector: ".actions" },
  ]);
});

test("desktop command bar must fit one horizontal row and stay within its panel", async () => {
  const selector = ".rrugc-stage5-toolbar";
  const assertion = { type: "single-row-within", selector, minViewportPx: 1280,
    items: [".search", ".sort", ".actions", ".stats"] };
  const page = (result, width = 1440) => ({
    viewportSize: () => ({ width, height: 900 }),
    locator: () => ({ first() { return this; }, evaluate: async () => result }),
  });
  await assert.doesNotReject(assertUiState(page({ reason: "", rows: [] }), assertion));
  await assert.doesNotReject(assertUiState(page({ reason: "ignored" }, 390), assertion));
  await assert.rejects(
    assertUiState(page({ reason: "clipped-or-outside-toolbar",
      rows: [{ selector: ".stats", right: 1400 }], containerRight: 1320 }), assertion),
    /single-row layout failed.*clipped-or-outside-toolbar/,
  );
  await assert.rejects(
    assertUiState(page({ reason: "controls-wrap-to-multiple-rows", rows: [] }), assertion),
    /single-row layout failed.*controls-wrap-to-multiple-rows/,
  );
});

test("scrollable table assertion requires a functioning independent scroll area", async () => {
  await assert.doesNotReject(
    assertUiState(fakePage({ overflow: 1 }), { type: "scrollable-x", selector: ".access-table-wrap" }),
  );
  await assert.rejects(
    assertUiState(fakePage({ overflow: 0 }), { type: "scrollable-x", selector: ".access-table-wrap" }),
    /independent horizontal scrolling/,
  );
});

test("meaningful state regressions fail the gate", async () => {
  await assert.rejects(
    assertUiState(fakePage({ text: "No image" }), { type: "text-includes", selector: "#status", value: "Completed" }),
    /expected text/,
  );
  await assert.rejects(
    assertUiState(fakePage({ overflow: 12 }), { type: "no-horizontal-overflow", selector: "#stage", maxPx: 2 }),
    /horizontal overflow/,
  );
  await assert.rejects(
    assertUiState(fakePage({ overflow: 5 }), { type: "no-viewport-overflow", selector: "html", maxPx: 0 }),
    /viewport overflow/,
  );
  await assert.rejects(
    assertUiState(fakePage({ undersizedControls: [{ name: "Mark suitable", width: 18, height: 44 }] }),
      { type: "min-control-size", selector: ".vote", minPx: 24 }),
    /undersized controls/,
  );
  await assert.rejects(
    assertUiState(fakePage({ count: 0 }), { type: "icon-visible", selector: ".missing" }),
    /missing\/invisible/,
  );
  await assert.rejects(
    assertUiState(fakePage(), { type: "unknown", selector: "#x" }), /Invalid UI QA assertion/,
  );
});
