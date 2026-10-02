#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BASE_REF="${CAM_STAGING_BASE_REF:-origin/main}"
FULL="${CAM_STAGING_FULL:-0}"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

note() {
  printf '\n==> %s\n' "$*"
}

command -v git >/dev/null 2>&1 || die "git is required"

if ! git rev-parse --verify "$BASE_REF^{commit}" >/dev/null 2>&1; then
  BASE_REF="main"
fi

git rev-parse --verify "$BASE_REF^{commit}" >/dev/null 2>&1 || die "Cannot resolve base ref"

BRANCH="$(git branch --show-current)"
if [[ "$BRANCH" == "main" && "${CAM_STAGING_ALLOW_MAIN:-0}" != "1" ]]; then
  die "Cursor staging work must run on a task branch, not main"
fi

CHANGED="$({
  git diff --name-only "$BASE_REF"...HEAD
  git diff --name-only
  git diff --name-only --cached
  git ls-files --others --exclude-standard
} | awk 'NF' | sort -u)"

note "Structural diff check"
git diff --check

if printf '%s\n' "$CHANGED" | grep -Eq '(^|/)(production\.env|\.env($|\.)|credentials?|secrets?)(/|$)' ; then
  die "Potential credential/environment file is present in the change set"
fi

frontend_changed=false
api_changed=false
integration_sensitive=false

if printf '%s\n' "$CHANGED" | grep -Eq '^(apps/client/|packages/(types|ui|utils)/)' ; then
  frontend_changed=true
fi

if printf '%s\n' "$CHANGED" | grep -Eq '^(apps/api/|apps/worker/|apps/inventory_worker/|packages/types/|deploy/|scripts/)' ; then
  api_changed=true
fi

if printf '%s\n' "$CHANGED" | grep -Eq '^(apps/api/(alembic|app/(core|modules/(auth|authorization|public_review|assets|pipeline|managed_storage|providers)))/|infrastructure/|deploy/|docs/security/|docs/architecture/)' ; then
  integration_sensitive=true
fi

if $frontend_changed; then
  note "Frontend locked install"
  (
    cd apps/client
    npm ci
    npm run typecheck
    npm test
    npm run build
  )
else
  note "Frontend unchanged; skipping frontend gate"
fi

if $api_changed; then
  note "Python syntax/import gate"
  python3 -m compileall -q apps/api/app apps/worker apps/inventory_worker

  if [[ -x apps/api/.venv/bin/python ]]; then
    note "API unit test gate"
    (
      cd apps/api
      timeout 12m .venv/bin/python -m pytest         tests/modules/authorization         tests/modules/public_review         tests/test_app_smoke.py -q
    )
  else
    note "apps/api/.venv is unavailable; compileall completed, pytest skipped"
  fi
else
  note "API/worker unchanged; skipping API gate"
fi

if [[ "$FULL" == "1" || "$integration_sensitive" == true ]]; then
  note "Full integration gate"
  make integration-test
fi

note "Staging gate passed"
printf 'Base: %s\n' "$BASE_REF"
printf 'Changed files:\n%s\n' "${CHANGED:-<none>}"
