import path from "node:path";
import { fileURLToPath } from "node:url";
import { UI_QA_PROFILES } from "./ui-qa-profiles.mjs";

const ALL_PROFILES = Object.keys(UI_QA_PROFILES);

function normalizeFiles(files) {
  return [...new Set(
    (files || [])
      .map((file) => String(file || "").replaceAll("\\", "/").trim())
      .filter(Boolean),
  )];
}

function add(set, ...profiles) {
  for (const profile of profiles) {
    if (UI_QA_PROFILES[profile]) set.add(profile);
  }
}

function isCrossWorkspaceFile(file) {
  return [
    "apps/client/app/main.tsx",
    "apps/client/app/AppRoute.tsx",
    "apps/client/styles/global.css",
    "apps/client/styles/ui-foundation.css",
    "apps/client/styles/responsive-platform.css",
    "apps/client/styles/workspace-page-header.css",
    "apps/client/app/components/ResponsiveWorkspaceNav.tsx",
    "apps/client/app/components/WorkspaceNavigation.tsx",
    "apps/client/app/components/WorkspacePageHeader.tsx",
    "apps/client/scripts/ui-qa-profiles.mjs",
    "apps/client/scripts/ui-qa-fixture.mjs",
  ].includes(file)
    || (
      file.startsWith("apps/client/scripts/ui-qa-")
      && !file.endsWith(".node-test.mjs")
    )
    || file.startsWith("apps/client/assets/fonts/")
    || file.startsWith("packages/ui/");
}

export function selectUiQaProfiles(changedFiles = []) {
  const files = normalizeFiles(changedFiles);

  if (files.some(isCrossWorkspaceFile)) {
    return {
      mode: "all",
      reason: "cross-workspace-ui-change",
      profiles: [...ALL_PROFILES],
    };
  }

  const selected = new Set();

  for (const file of files) {
    if (file.startsWith("apps/client/visual-baselines/")) {
      const profile = file.split("/")[3];
      add(selected, profile);
      continue;
    }

    if (file.includes("/review-board/") || file === "apps/client/styles/review-board.css") {
      add(selected, "review-board");
    }
    if (
      file.includes("/realistic-review-ugc/")
      || file.endsWith("/realistic-review-ugc/ui-overhaul.css")
    ) {
      add(selected, "realistic-review-ugc");
    }
    if (
      file.includes("/ai-operations/")
      || file.includes("/features/ai_operations/")
      || file === "apps/client/styles/ai-operations.css"
    ) {
      add(selected, "ai-operations", "job-queue");
    }
    if (
      file.includes("/inventory/")
      || file === "apps/client/styles/inventory.css"
    ) {
      add(selected, "inventory", "ai-operations");
    }
    if (
      file.includes("/access-management/")
      || file.includes("/features/access_management/")
      || file === "apps/client/styles/access-management.css"
    ) {
      add(selected, "access-management");
    }
    if (file.includes("/video-generation/")) {
      add(selected, "video-generation");
    }
    if (file.includes("/job-queue/")) {
      add(selected, "job-queue");
    }
    if (
      file.includes("/public-review/")
      || file === "apps/client/styles/public-review.css"
    ) {
      add(selected, "public-review");
      if (file.endsWith("/RichAnnotation.tsx")) add(selected, "review-board");
    }
    if (
      file.includes("/components/Asset")
      || file.includes("/hooks/useDriveExplorer")
      || file.includes("/hooks/useSearch")
      || file.includes("/hooks/useVisualSearch")
      || file === "apps/client/app/App.tsx"
    ) {
      add(selected, "explorer-viewer");
    }
    if (file.startsWith("apps/client/scripts/fixtures/")) {
      const base = path.basename(file, ".json");
      add(selected, base);
    }
    if (file.startsWith("docs/operations/ui-qa-") && file.endsWith("-plan.json")) {
      const base = path.basename(file).replace(/^ui-qa-/, "").replace(/-plan\.json$/, "");
      add(selected, base);
    }
  }

  if (selected.size === 0) {
    return {
      mode: "default",
      reason: files.length === 0 ? "no-changed-files" : "no-specific-profile-match",
      profiles: ["explorer-viewer"],
    };
  }

  return {
    mode: selected.size === ALL_PROFILES.length ? "all" : "selected",
    reason: "changed-files-profile-match",
    profiles: ALL_PROFILES.filter((profile) => selected.has(profile)),
  };
}

function argValue(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : undefined;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const changedRaw = argValue("--changed") || process.env.CAM_UI_CHANGED_FILES || "";
  const changedFiles = String(changedRaw)
    .split(/[\r\n,]+/)
    .map((item) => item.trim())
    .filter(Boolean);
  const result = selectUiQaProfiles(changedFiles);

  if (process.argv.includes("--format-lines")) {
    console.log(result.mode);
    console.log(result.reason);
    for (const profile of result.profiles) console.log(profile);
  } else {
    console.log(JSON.stringify(result, null, 2));
  }
}
