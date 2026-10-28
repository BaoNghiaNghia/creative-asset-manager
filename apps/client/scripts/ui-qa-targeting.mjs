export function parseCsvList(rawValue) {
  return String(rawValue || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

export function sanitizeStateName(value) {
  return String(value || "state")
    .replace(/[^a-zA-Z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "") || "state";
}

export function selectPlanSteps(steps, requestedNames = []) {
  if (!Array.isArray(steps) || steps.length === 0) {
    throw new Error("UI QA plan must contain a non-empty steps array.");
  }

  const requested = [...new Set(requestedNames.map(sanitizeStateName))];
  if (requested.length === 0) return steps;

  const byName = new Map(
    steps.map((step) => [sanitizeStateName(step.name || "state"), step]),
  );
  const missing = requested.filter((name) => !byName.has(name));
  if (missing.length > 0) {
    throw new Error(
      `Unknown UI QA state(s): ${missing.join(", ")}. Available: ${[
        ...byName.keys(),
      ].join(", ")}`,
    );
  }

  return requested.map((name) => byName.get(name));
}

export function selectExecutionSteps(steps, requestedNames = []) {
  const requested = [...new Set(requestedNames.map(sanitizeStateName))];
  if (requested.length === 0) return steps;

  const captureSteps = selectPlanSteps(steps, requested);
  const lastIndex = Math.max(...captureSteps.map((step) => steps.indexOf(step)));
  return steps.slice(0, lastIndex + 1);
}

export function initialQaRoute(baseUrl, executionSteps) {
  const first = executionSteps?.[0];
  if (!first?.goto || first.resetBefore) {
    return { url: new URL(baseUrl).href, skipFirstGoto: false };
  }
  const base = new URL(baseUrl);
  const target = new URL(String(first.goto), base);
  if (target.origin !== base.origin) {
    throw new Error("UI QA navigation must remain on the fixture preview origin");
  }
  return { url: target.href, skipFirstGoto: true };
}

export function collectRepairTargets(analysis, maxTargets = 4) {
  const limit = Math.max(1, Number(maxTargets) || 4);
  const issues = Array.isArray(analysis?.issues) ? analysis.issues : [];
  const selected = issues
    .filter((issue) =>
      ["missing-baseline", "dimension-mismatch", "mismatch"].includes(
        issue?.status,
      ),
    )
    .slice(0, limit);

  const viewports = [];
  const states = [];
  for (const issue of selected) {
    if (issue.viewport && !viewports.includes(issue.viewport)) {
      viewports.push(issue.viewport);
    }
    const state = sanitizeStateName(issue.state || issue.screenshot || "state");
    if (state && !states.includes(state)) states.push(state);
  }

  return {
    issueCount: issues.length,
    selectedIssueCount: selected.length,
    viewports,
    states,
  };
}
