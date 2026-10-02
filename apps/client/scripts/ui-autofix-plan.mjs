import crypto from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SUPPORTED_STATES = [
  "default",
  "hover-card",
  "selected",
  "selected-hover",
  "search-results",
  "search-focus",
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
  if (searchLike) states.push("search-results", "search-focus");
  if (includesAny(text, ["hover"])) states.push("hover-card");
  if (includesAny(text, ["selected", "selection", "chọn"])) {
    states.push("selected", "selected-hover");
  }
  if (includesAny(text, ["focus", "keyboard"])) states.push(searchLike ? "search-focus" : "default");

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

  const normalizedStates = unique(states).filter((state) => SUPPORTED_STATES.includes(state));
  const profileSupported = scope === "asset-explorer";

  return {
    scope,
    profile: profileSupported ? "explorer-viewer" : null,
    visualProfileSupported: profileSupported,
    viewports: unique(viewports),
    states: normalizedStates.slice(0, 3),
    reason: profileSupported
      ? "fixture-backed-asset-explorer-profile"
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
