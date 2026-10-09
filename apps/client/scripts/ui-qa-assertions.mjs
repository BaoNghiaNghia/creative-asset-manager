/** Fixture-backed interaction contracts. Assertions are read-only; no backend mutation. */
export async function assertUiState(page, assertion) {
  const type = String(assertion?.type || "");
  const selector = String(assertion?.selector || "");
  if (!selector || !["visible", "hidden", "text-includes", "count", "attribute",
                      "checked", "no-horizontal-overflow", "icon-visible"].includes(type)) {
    throw new Error(`Invalid UI QA assertion type/selector: ${type} ${selector}`);
  }
  const locator = page.locator(selector);
  const timeout = Math.min(10000, Math.max(100, Number(assertion.timeoutMs || 4000)));
  if (type === "visible" || type === "hidden") {
    await locator.first().waitFor({ state: type, timeout });
  } else if (type === "text-includes") {
    const text = await locator.first().innerText({ timeout });
    if (!text.includes(String(assertion.value ?? "")) || !String(assertion.value ?? "")) {
      throw new Error(`UI QA expected text "${assertion.value}" at ${selector}; got: ${text.slice(0, 120)}`);
    }
  } else if (type === "count") {
    const count = await locator.count();
    if (count !== Number(assertion.value) || !Number.isInteger(Number(assertion.value))) {
      throw new Error(`UI QA expected ${assertion.value} matches at ${selector}; got ${count}`);
    }
  } else if (type === "attribute") {
    const actual = await locator.first().getAttribute(String(assertion.name || ""));
    if (!assertion.name || actual !== String(assertion.value)) {
      throw new Error(`UI QA expected ${selector} [${assertion.name}=${assertion.value}]; got ${actual}`);
    }
  } else if (type === "checked") {
    const checked = await locator.first().isChecked();
    if (checked !== Boolean(assertion.value)) {
      throw new Error(`UI QA expected checked=${Boolean(assertion.value)} at ${selector}; got ${checked}`);
    }
  } else if (type === "no-horizontal-overflow") {
    const overflow = await locator.first().evaluate(element => element.scrollWidth - element.clientWidth);
    const max = Number(assertion.maxPx ?? 2);
    if (overflow > max) throw new Error(`UI QA horizontal overflow at ${selector}: ${overflow}px > ${max}px`);
  } else if (type === "icon-visible") {
    const missing = await locator.evaluateAll(nodes => nodes.filter(node => {
      const style = getComputedStyle(node);
      const rect = node.getBoundingClientRect();
      if (style.display === "none" || style.visibility === "hidden" ||
          rect.width === 0 || rect.height === 0) return false;
      const svg = node.querySelector("svg[data-ui-icon]");
      if (!svg) return true;
      const iconStyle = getComputedStyle(svg);
      const iconRect = svg.getBoundingClientRect();
      return iconStyle.display === "none" || iconStyle.visibility === "hidden" ||
        iconStyle.opacity === "0" || iconRect.width < 8 || iconRect.height < 8;
    }).map(node => (node.textContent || node.getAttribute("aria-label") || "").trim().slice(0, 70)));
    if (!await locator.count() || missing.length) {
      throw new Error(`UI QA icon missing/invisible at ${selector}: ${missing.join(", ")}`);
    }
  }
}

export async function assertUiStep(page, assertions = []) {
  if (!Array.isArray(assertions)) throw new Error("UI QA assertions must be an array.");
  for (const assertion of assertions) await assertUiState(page, assertion);
}
