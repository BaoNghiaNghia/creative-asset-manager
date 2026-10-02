import crypto from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { profileForScope } from "./ui-qa-profiles.mjs";

const SUPPORTED_STATES = [
  "default",
  "hover-card",
  "selected",
  "selected-hover",
  "search-results",
  "search-focus",
  "issue-hover",
  "reviewer-focus",
  "date-filters",
  "campaign-hover",
  "candidate-hover",
  "candidate-filter-focus",
  "kpi-hover",
  "filter-focus",
  "material-hover",
  "material-search-focus",
  "candidate-hover",
  "member-row-hover",
  "member-search-focus",
  "roles-tab",
  "generation-card-hover",
  "prompt-focus",
  "reference-selected",
  "queue-row-hover",
  "queue-search-focus",
  "queue-completed-tab",
  "queue-result-modal",
  "public-card-hover",
  "public-search-focus",
  "public-media-open",
];

function unique(values) {
  return [...new Set(values.filter(Boolean))];
}

function includesAny(text, needles) {
  return needles.some((needle) => text.includes(needle));
}

function normalizeFiles(files) {
  return unique(
    (files || [])
      .map((item) => String(item || "").replaceAll("\\", "/").trim())
      .filter(Boolean),
  );
}

export function classifyUiTask(task, changedFiles = []) {
  const text = String(task || "").trim().toLocaleLowerCase();
  const files = normalizeFiles(changedFiles);
  const joinedFiles = files.join("\n").toLocaleLowerCase();

  let scope = "asset-explorer";
  if (includesAny(text, ["review board", "review-board"]) || joinedFiles.includes("/review-board/")) {
    scope = "review-board";
  } else if (
    includesAny(text, ["realistic review ugc", "realistic-review-ugc", "pinterest scout"]) ||
    joinedFiles.includes("/realistic-review-ugc/")
  ) {
    scope = "realistic-review-ugc";
  } else if (includesAny(text, ["inventory"]) || joinedFiles.includes("/inventory/")) {
    scope = "inventory";
  } else if (includesAny(text, ["access management", "access-management", "settings/access"]) || joinedFiles.includes("/access-management/")) {
    scope = "access-management";
  } else if (includesAny(text, ["video generation", "video-generation"]) || joinedFiles.includes("/video-generation/")) {
    scope = "video-generation";
  } else if (includesAny(text, ["job queue", "job-queue"]) || joinedFiles.includes("/job-queue/")) {
    scope = "job-queue";
  } else if (includesAny(text, ["public review", "shared review", "share review"]) || joinedFiles.includes("/public-review/")) {
    scope = "public-review";
  } else if (includesAny(text, ["ai operations"]) || joinedFiles.includes("/ai-operations/")) {
    scope = "ai-operations";
  }

  const viewports = [];
  if (includesAny(text, ["responsive", "all screen", "all viewport", "different screen", "nhiều kích thước", "responsive"])) {
    viewports.push("desktop", "tabletPortrait", "mobile");
  } else {
    if (includesAny(text, ["desktop", "pc", "1440", "1280"])) viewports.push("desktop");
    if (includesAny(text, ["tablet", "ipad", "768", "1024"])) viewports.push("tabletPortrait");
    if (includesAny(text, ["mobile", "phone", "điện thoại", "390"])) viewports.push("mobile");
  }

  if (viewports.length === 0) {
    if (
      joinedFiles.includes("global.css") ||
      joinedFiles.includes("assetgrid") ||
      includesAny(text, ["font", "spacing", "layout", "grid", "card size", "responsive"])
    ) {
      viewports.push("desktop", "tabletPortrait", "mobile");
    } else {
      viewports.push("desktop");
    }
  }

  const states = [];
  const searchLike =
    includesAny(text, ["search", "filter", "tìm kiếm"]) ||
    joinedFiles.includes("searchcontrols") ||
    joinedFiles.includes("usesearch");
  const hoverLike = includesAny(text, ["hover"]);
  const focusLike = includesAny(text, ["focus", "keyboard"]);

  if (scope === "review-board") {
    states.push("default");
    if (hoverLike) states.push("issue-hover");
    if (searchLike || focusLike) states.push("reviewer-focus");
    if (includesAny(text, ["date", "ngày"])) states.push("date-filters");
  } else if (scope === "realistic-review-ugc") {
    states.push("default");
    if (hoverLike || includesAny(text, ["campaign", "card"])) states.push("campaign-hover");
    if (hoverLike || includesAny(text, ["candidate", "reference", "grid"])) states.push("candidate-hover");
    if (searchLike || focusLike) states.push("candidate-filter-focus");
  } else if (scope === "ai-operations") {
    states.push("default");
    if (hoverLike || includesAny(text, ["kpi", "card"])) states.push("kpi-hover");
    if (searchLike || focusLike) states.push("filter-focus");
  } else if (scope === "inventory") {
    states.push("default");
    if (hoverLike || includesAny(text, ["material", "vật tư", "card"])) states.push("material-hover");
    if (searchLike || focusLike) states.push("material-search-focus");
    if (hoverLike || includesAny(text, ["candidate", "review", "gợi ý"])) states.push("candidate-hover");
  } else if (scope === "access-management") {
    states.push("default");
    if (hoverLike || includesAny(text, ["member", "thành viên", "row"])) states.push("member-row-hover");
    if (searchLike || focusLike) states.push("member-search-focus");
    if (includesAny(text, ["role", "roles", "quyền"])) states.push("roles-tab");
  } else if (scope === "video-generation") {
    states.push("default");
    if (focusLike || includesAny(text, ["prompt"])) states.push("prompt-focus");
    if (includesAny(text, ["reference", "image", "ảnh"])) states.push("reference-selected");
    if (hoverLike || includesAny(text, ["card"])) states.push("generation-card-hover");
  } else if (scope === "job-queue") {
    states.push("default");
    if (hoverLike || includesAny(text, ["row", "job"])) states.push("queue-row-hover");
    if (searchLike || focusLike) states.push("queue-search-focus");
    if (includesAny(text, ["completed", "status", "tab"])) states.push("queue-completed-tab");
    if (includesAny(text, ["result", "modal", "comparison"])) states.push("queue-result-modal");
  } else if (scope === "public-review") {
    states.push("default");
    if (hoverLike || includesAny(text, ["card", "thumbnail"])) states.push("public-card-hover");
    if (searchLike || focusLike) states.push("public-search-focus");
    if (includesAny(text, ["viewer", "media", "comment", "review"])) states.push("public-media-open");
  } else {
    if (searchLike) states.push("search-results", "search-focus");
    if (hoverLike) states.push("hover-card");
    if (includesAny(text, ["selected", "selection", "chọn"])) {
      states.push("selected", "selected-hover");
    }
    if (focusLike) states.push(searchLike ? "search-focus" : "default");

    if (states.length === 0) {
      if (
        joinedFiles.includes("assetgrid") ||
        includesAny(text, ["card", "grid", "folder", "title", "thumbnail", "font", "spacing", "border"])
      ) {
        states.push("default", "hover-card", "selected-hover");
      } else {
        states.push("default");
      }
    }
  }

  const normalizedStates = unique(states).filter((state) => SUPPORTED_STATES.includes(state));
  const profile = profileForScope(scope);

  return {
    scope,
    profile: profile?.name || null,
    visualProfileSupported: Boolean(profile),
    viewports: unique(viewports),
    states: normalizedStates.slice(0, 3),
    reason: profile
      ? `fixture-backed-${profile.name}-profile`
      : `no-fixture-backed-visual-profile-for:${scope}`,
  };
}

export function buildAutofixSessionId(task, baseCommit = "") {
  return crypto
    .createHash("sha256")
    .update(String(baseCommit || "workspace"))
    .update("\0")
    .update(String(task || "ui-task"))
    .digest("hex")
    .slice(0, 16);
}

function argValue(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : undefined;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const task = argValue("--task") || process.env.CAM_UI_TASK || "";
  const changedRaw = argValue("--changed") || process.env.CAM_UI_CHANGED_FILES || "";
  const changedFiles = String(changedRaw)
    .split(/[\r\n,]+/)
    .map((item) => item.trim())
    .filter(Boolean);
  const baseCommit = argValue("--base") || process.env.CAM_UI_BASE_COMMIT || "";
  const result = classifyUiTask(task, changedFiles);
  console.log(
    JSON.stringify(
      {
        schemaVersion: 1,
        sessionId: buildAutofixSessionId(task, baseCommit),
        task,
        changedFiles,
        ...result,
      },
      null,
      2,
    ),
  );
}
