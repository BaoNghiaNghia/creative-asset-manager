#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BASE_REF="${CAM_UI_BASE_REF:-origin/main}"
FORCE="${CAM_UI_FORCE:-0}"

note() {
  printf '\n==> %s\n' "$*"
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

command -v git >/dev/null 2>&1 || die "git is required"
command -v node >/dev/null 2>&1 || die "node is required"
command -v npm >/dev/null 2>&1 || die "npm is required"

if ! git rev-parse --verify "$BASE_REF^{commit}" >/dev/null 2>&1; then
  BASE_REF="HEAD"
fi

CHANGED="$({
  git diff --name-only "$BASE_REF"...HEAD 2>/dev/null || true
  git diff --name-only
  git diff --name-only --cached
  git ls-files --others --exclude-standard
} | awk 'NF' | sort -u)"

FRONTEND_CHANGED=false
if printf '%s\n' "$CHANGED" | grep -Eq '^(apps/client/|packages/(types|ui|utils)/)'; then
  FRONTEND_CHANGED=true
fi

if [[ "$FRONTEND_CHANGED" != true && "$FORCE" != "1" ]]; then
  note "No frontend/UI changes detected; UI gate skipped"
  exit 0
fi

note "Structural diff check"
git diff --check

note "Frontend dependency preflight"
if [[ ! -d apps/client/node_modules ]]; then
  (cd apps/client && npm ci)
fi

note "TypeScript"
(cd apps/client && npm run typecheck)

note "Frontend tests"
if [[ -n "${CAM_UI_TESTS:-}" ]]; then
  read -r -a TEST_ARGS <<< "$CAM_UI_TESTS"
  (cd apps/client && npm test -- "${TEST_ARGS[@]}")
else
  (cd apps/client && npm test)
fi

note "Production frontend build"
(cd apps/client && npm run build)

if [[ -n "${CAM_UI_QA_URL:-}" ]]; then
  note "Responsive Browser QA"
  QA_ARGS=(--url "$CAM_UI_QA_URL")
  if [[ -n "${CAM_UI_QA_PLAN:-}" ]]; then
    QA_ARGS+=(--plan "$CAM_UI_QA_PLAN")
  fi
  if [[ -n "${CAM_UI_QA_FIXTURE:-}" ]]; then
    QA_ARGS+=(--fixture "$CAM_UI_QA_FIXTURE")
  fi
  if [[ -n "${CAM_UI_VIEWPORTS:-}" ]]; then
    QA_ARGS+=(--viewports "$CAM_UI_VIEWPORTS")
  fi
  if [[ "${CAM_UI_QA_STRICT:-0}" == "1" ]]; then
    QA_ARGS+=(--strict)
  fi
  (cd apps/client && npm run ui:qa -- "${QA_ARGS[@]}")
elif [[ "${CAM_UI_QA_SKIP:-0}" != "1" ]]; then
  note "Authenticated local staging Browser QA"
  bash scripts/cam-ui-staging-qa.sh
else
  note "Browser QA explicitly skipped with CAM_UI_QA_SKIP=1"
fi

note "UI gate passed"
printf 'Base: %s\n' "$BASE_REF"
printf 'Changed files:\n%s\n' "${CHANGED:-<none>}"
