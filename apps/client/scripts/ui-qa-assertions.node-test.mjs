import assert from "node:assert/strict";
import test from "node:test";
import { assertUiStep, assertUiState } from "./ui-qa-assertions.mjs";

function fakePage({ text = "Ready for review", count = 2, overflow = 0, visible = true } = {}) {
  return { locator(selector) {
    return {
      count: async () => count,
      first() { return this; },
      innerText: async () => text,
      waitFor: async ({ state }) => { if (state === "visible" && !visible) throw Error("not visible"); },
      getAttribute: async name => name === "aria-label" ? "Review" : null,
      isChecked: async () => true,
      evaluate: async () => overflow,
      evaluateAll: async () => [],
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
    { type: "icon-visible", selector: ".actions" },
  ]);
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
    assertUiState(fakePage({ count: 0 }), { type: "icon-visible", selector: ".missing" }),
    /missing\/invisible/,
  );
  await assert.rejects(
    assertUiState(fakePage(), { type: "unknown", selector: "#x" }), /Invalid UI QA assertion/,
  );
});
