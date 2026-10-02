import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const CLIENT_ROOT = path.resolve(SCRIPT_DIR, "..");
const REPO_ROOT = path.resolve(CLIENT_ROOT, "../..");

const CODE_EXTENSIONS = [".ts", ".tsx", ".js", ".jsx", ".mjs", ".css"];
const TEST_RE = /\.(?:test|spec)\.(?:ts|tsx|js|jsx)$/;
const SOURCE_RE = /\.(?:ts|tsx|js|jsx|css)$/;
const FULL_SUITE_PATHS = new Set([
  "apps/client/app/App.tsx",
  "apps/client/app/AppRoute.tsx",
  "apps/client/app/Provider.tsx",
  "apps/client/app/Router.tsx",
  "apps/client/app/Store.ts",
  "apps/client/app/main.tsx",
  "apps/client/app/types.ts",
  "apps/client/package.json",
  "apps/client/vite.config.ts",
  "apps/client/tsconfig.json",
  "apps/client/tsconfig.app.json",
]);
const SECURITY_PATH_MARKERS = [
  "/access-management/",
  "/public-review/",
  "/public-review-management/",
];
const AUTOMATION_ONLY_PREFIXES = [
  "apps/client/scripts/",
  "apps/client/visual-baselines/",
  "docs/",
  "deploy/tests/",
  "scripts/",
];

function normalize(value) {
  return String(value || "").replaceAll("\\", "/").replace(/^\.\//, "");
}

function unique(values) {
  return [...new Set(values.filter(Boolean))];
}

function isVisualOnly(filePath) {
  return /\.(?:css|scss)$/.test(filePath);
}

function isAutomationOnly(filePath) {
  return AUTOMATION_ONLY_PREFIXES.some((prefix) => filePath.startsWith(prefix));
}

function isSecuritySensitive(filePath) {
  return SECURITY_PATH_MARKERS.some((marker) => filePath.includes(marker));
}

async function walk(directory) {
  const files = [];
  const entries = await fs.readdir(directory, { withFileTypes: true });
  for (const entry of entries) {
    if (entry.name === "node_modules" || entry.name === "dist" || entry.name === ".ui-qa") {
      continue;
    }
    const fullPath = path.join(directory, entry.name);
    if (entry.isDirectory()) files.push(...(await walk(fullPath)));
    else files.push(fullPath);
  }
  return files;
}

function stripExtension(filePath) {
  for (const extension of CODE_EXTENSIONS) {
    if (filePath.endsWith(extension)) return filePath.slice(0, -extension.length);
  }
  return filePath;
}

function resolveRelativeImport(importerAbsolute, specifier, knownFiles) {
  if (!specifier.startsWith(".")) return null;
  const base = path.resolve(path.dirname(importerAbsolute), specifier);
  const candidates = [
    base,
    ...CODE_EXTENSIONS.map((extension) => base + extension),
    ...CODE_EXTENSIONS.map((extension) => path.join(base, "index" + extension)),
  ];
  return candidates.find((candidate) => knownFiles.has(candidate)) || null;
}

function importsFromSource(source) {
  const imports = [];
  const patterns = [
    /(?:import|export)\s+(?:[^"'\n]+?\s+from\s+)?["']([^"']+)["']/g,
    /import\s*\(\s*["']([^"']+)["']\s*\)/g,
  ];
  for (const pattern of patterns) {
    for (const match of source.matchAll(pattern)) imports.push(match[1]);
  }
  return unique(imports);
}

function basenameCandidates(changedRepoPath, testRepoPaths) {
  const filename = path.posix.basename(changedRepoPath);
  const stem = stripExtension(filename);
  if (!stem) return [];
  return testRepoPaths.filter((testPath) => {
    const testName = path.posix.basename(testPath);
    return testName === `${stem}.test.ts` ||
      testName === `${stem}.test.tsx` ||
      testName.startsWith(`${stem}.`) && TEST_RE.test(testName);
  });
}

export async function selectSmartTests({
  changedFiles,
  maxTests = 12,
  repoRoot = REPO_ROOT,
} = {}) {
  const normalizedChanged = unique((changedFiles || []).map(normalize));
  const frontendChanged = normalizedChanged.filter(
    (filePath) =>
      filePath.startsWith("apps/client/") ||
      filePath.startsWith("packages/types/") ||
      filePath.startsWith("packages/ui/") ||
      filePath.startsWith("packages/utils/"),
  );

  if (frontendChanged.length === 0) {
    return {
      mode: "none",
      reason: "no-frontend-code-changed",
      tests: [],
      changedFiles: normalizedChanged,
    };
  }

  if (
    frontendChanged.some(
      (filePath) =>
        FULL_SUITE_PATHS.has(filePath) ||
        filePath.startsWith("packages/") ||
        isSecuritySensitive(filePath),
    )
  ) {
    return {
      mode: "full",
      reason: "broad-or-security-sensitive-frontend-change",
      tests: [],
      changedFiles: normalizedChanged,
    };
  }

  const clientRoot = path.join(repoRoot, "apps/client");
  const appRoot = path.join(clientRoot, "app");
  const allAbsolute = (await walk(appRoot)).filter((filePath) =>
    CODE_EXTENSIONS.some((extension) => filePath.endsWith(extension)),
  );
  const knownFiles = new Set(allAbsolute);
  const testAbsolute = allAbsolute.filter((filePath) => TEST_RE.test(filePath));
  const testRepoPaths = testAbsolute.map((filePath) =>
    normalize(path.relative(repoRoot, filePath)),
  );

  const reverse = new Map();
  for (const absolutePath of allAbsolute) {
    if (!SOURCE_RE.test(absolutePath) && !TEST_RE.test(absolutePath)) continue;
    let source = "";
    try {
      source = await fs.readFile(absolutePath, "utf8");
    } catch {
      continue;
    }
    for (const specifier of importsFromSource(source)) {
      const dependency = resolveRelativeImport(absolutePath, specifier, knownFiles);
      if (!dependency) continue;
      const callers = reverse.get(dependency) || [];
      callers.push(absolutePath);
      reverse.set(dependency, callers);
    }
  }

  const selected = new Set();
  let uncertainCode = false;
  let onlyVisualOrAutomation = true;

  for (const changedRepoPath of frontendChanged) {
    if (isAutomationOnly(changedRepoPath)) continue;
    if (TEST_RE.test(changedRepoPath)) {
      selected.add(changedRepoPath);
      onlyVisualOrAutomation = false;
      continue;
    }
    if (isVisualOnly(changedRepoPath)) {
      for (const candidate of basenameCandidates(changedRepoPath, testRepoPaths)) {
        selected.add(candidate);
      }
      continue;
    }

    onlyVisualOrAutomation = false;
    const absoluteChanged = path.resolve(repoRoot, changedRepoPath);
    if (!knownFiles.has(absoluteChanged)) {
      uncertainCode = true;
      continue;
    }

    const localTests = basenameCandidates(changedRepoPath, testRepoPaths);
    for (const candidate of localTests) {
      selected.add(candidate);
    }

    const visited = new Set([absoluteChanged]);
    let frontier = [absoluteChanged];
    const graphDepth = localTests.length > 0 ? 1 : 3;
    for (let depth = 0; depth < graphDepth && frontier.length > 0; depth += 1) {
      const next = [];
      for (const dependency of frontier) {
        for (const caller of reverse.get(dependency) || []) {
          if (visited.has(caller)) continue;
          visited.add(caller);
          const repoPath = normalize(path.relative(repoRoot, caller));
          if (TEST_RE.test(repoPath)) selected.add(repoPath);
          else next.push(caller);
        }
      }
      frontier = next;
    }

    if (selected.size === 0) uncertainCode = true;
  }

  const tests = [...selected].sort();
  if (tests.length > maxTests) {
    return {
      mode: "full",
      reason: `targeted-test-set-too-large:${tests.length}`,
      tests: [],
      changedFiles: normalizedChanged,
    };
  }
  if (tests.length > 0) {
    return {
      mode: "targeted",
      reason: "dependency-linked-tests",
      tests,
      changedFiles: normalizedChanged,
    };
  }
  if (uncertainCode && !onlyVisualOrAutomation) {
    return {
      mode: "full",
      reason: "frontend-code-change-without-confident-test-map",
      tests: [],
      changedFiles: normalizedChanged,
    };
  }
  return {
    mode: "none",
    reason: onlyVisualOrAutomation
      ? "visual-or-automation-only-change"
      : "no-relevant-tests",
    tests: [],
    changedFiles: normalizedChanged,
  };
}

function argValue(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : undefined;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const changedRaw =
    argValue("--changed") ||
    process.env.CAM_UI_CHANGED_FILES ||
    "";
  const changedFiles = String(changedRaw)
    .split(/[\r\n,]+/)
    .map((item) => item.trim())
    .filter(Boolean);
  const maxTests = Number(argValue("--max-tests") || process.env.CAM_UI_SMART_TEST_MAX || 12);
  const result = await selectSmartTests({ changedFiles, maxTests });
  const format = argValue("--format") || "json";
  if (format === "lines") {
    console.log(result.mode);
    console.log(result.reason);
    for (const testPath of result.tests) {
      console.log(normalize(path.relative(CLIENT_ROOT, path.resolve(REPO_ROOT, testPath))));
    }
  } else {
    console.log(JSON.stringify(result, null, 2));
  }
}
