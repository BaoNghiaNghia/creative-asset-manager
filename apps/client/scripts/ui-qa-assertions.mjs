/** Fixture-backed interaction contracts. Assertions are read-only; no backend mutation. */
export async function assertUiState(page, assertion) {
  const type = String(assertion?.type || "");
  const selector = String(assertion?.selector || "");
  if (!selector || !["visible", "hidden", "text-includes", "count", "attribute",
                      "checked", "no-horizontal-overflow", "no-viewport-overflow",
                      "min-control-size", "scrollable-x", "icon-visible"].includes(type)) {
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
  } else if (type === "no-viewport-overflow") {
    const overflow = await locator.first().evaluate(
      () => Math.max(0, document.documentElement.scrollWidth - window.innerWidth),
    );
    const max = Number(assertion.maxPx ?? 0);
    if (overflow > max) {
      throw new Error(`UI QA viewport overflow: ${overflow}px > ${max}px`);
    }
  } else if (type === "min-control-size") {
    const minimum = Number(assertion.minPx ?? 24);
    if (!Number.isFinite(minimum) || minimum < 1 || minimum > 100) {
      throw new Error("UI QA control minimum must be between 1 and 100px");
    }
    const missing = await locator.evaluateAll((nodes, minPx) => nodes.filter(node => {
      const style = getComputedStyle(node);
      const box = node.getBoundingClientRect();
      if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") return false;
      return box.width > 0 && box.height > 0 && (box.width < minPx || box.height < minPx);
    }).map(node => ({ name: (node.getAttribute("aria-label") || node.textContent || "").trim().slice(0, 60),
      width: Math.round(node.getBoundingClientRect().width),
      height: Math.round(node.getBoundingClientRect().height) })), minimum);
    if (!await locator.count() || missing.length) {
      throw new Error(`UI QA undersized controls at ${selector}: ${JSON.stringify(missing)}`);
    }
  } else if (type === "scrollable-x") {
    const scrollable = await locator.first().evaluate(element => {
      const before = element.scrollLeft;
      if (element.scrollWidth <= element.clientWidth) return false;
      element.scrollLeft = element.scrollWidth;
      const moved = element.scrollLeft !== before;
      element.scrollLeft = before;
      return moved;
    });
    if (!scrollable) throw new Error(`UI QA expected independent horizontal scrolling at ${selector}`);
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
