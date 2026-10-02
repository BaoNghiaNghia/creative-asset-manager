import path from "node:path";
import { fileURLToPath } from "node:url";

export const UI_QA_PROFILES = Object.freeze({
  "explorer-viewer": {
    scope: "asset-explorer",
    route: "/",
    fixture: "apps/client/scripts/fixtures/explorer-viewer.json",
    plan: "docs/operations/ui-qa-explorer-viewer-plan.json",
    baseline: "apps/client/visual-baselines/explorer-viewer",
  },
  "review-board": {
    scope: "review-board",
    route: "/review-board",
    fixture: "apps/client/scripts/fixtures/review-board.json",
    plan: "docs/operations/ui-qa-review-board-plan.json",
    baseline: "apps/client/visual-baselines/review-board",
  },
  "realistic-review-ugc": {
    scope: "realistic-review-ugc",
    route: "/realistic-review-ugc",
    fixture: "apps/client/scripts/fixtures/realistic-review-ugc.json",
    plan: "docs/operations/ui-qa-realistic-review-ugc-plan.json",
    baseline: "apps/client/visual-baselines/realistic-review-ugc",
  },
  "ai-operations": {
    scope: "ai-operations",
    route: "/ai-operations",
    fixture: "apps/client/scripts/fixtures/ai-operations.json",
    plan: "docs/operations/ui-qa-ai-operations-plan.json",
    baseline: "apps/client/visual-baselines/ai-operations",
  },
  "inventory": {
    scope: "inventory",
    route: "/inventory/materials",
    fixture: "apps/client/scripts/fixtures/inventory.json",
    plan: "docs/operations/ui-qa-inventory-plan.json",
    baseline: "apps/client/visual-baselines/inventory",
  },
  "access-management": {
    scope: "access-management",
    route: "/settings/access",
    fixture: "apps/client/scripts/fixtures/access-management.json",
    plan: "docs/operations/ui-qa-access-management-plan.json",
    baseline: "apps/client/visual-baselines/access-management",
  },
  "video-generation": {
    scope: "video-generation",
    route: "/video-generation",
    fixture: "apps/client/scripts/fixtures/video-generation.json",
    plan: "docs/operations/ui-qa-video-generation-plan.json",
    baseline: "apps/client/visual-baselines/video-generation",
  },
  "job-queue": {
    scope: "job-queue",
    route: "/job-queue",
    fixture: "apps/client/scripts/fixtures/job-queue.json",
    plan: "docs/operations/ui-qa-job-queue-plan.json",
    baseline: "apps/client/visual-baselines/job-queue",
  },
  "public-review": {
    scope: "public-review",
    route: "/share/qa-review",
    fixture: "apps/client/scripts/fixtures/public-review.json",
    plan: "docs/operations/ui-qa-public-review-plan.json",
    baseline: "apps/client/visual-baselines/public-review",
  },
});

export function getUiQaProfile(name = "explorer-viewer") {
  const profile = UI_QA_PROFILES[name];
  if (!profile) {
    throw new Error(
      `Unknown UI QA profile "${name}". Available: ${Object.keys(UI_QA_PROFILES).join(", ")}`,
    );
  }
  return { name, ...profile };
}

export function profileForScope(scope) {
  for (const [name, profile] of Object.entries(UI_QA_PROFILES)) {
    if (profile.scope === scope) return { name, ...profile };
  }
  return null;
}

function argValue(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : undefined;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const name = argValue("--profile") || process.env.CAM_UI_QA_PROFILE || "explorer-viewer";
  const profile = getUiQaProfile(name);
  if (process.argv.includes("--format-lines")) {
    console.log(profile.route);
    console.log(profile.fixture);
    console.log(profile.plan);
    console.log(profile.baseline);
    console.log(profile.scope);
  } else {
    console.log(JSON.stringify(profile, null, 2));
  }
}
