#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CLIENT="$ROOT/apps/client"
ENV_FILE="${CAM_PRODUCTION_ENV_FILE:-/etc/creative-asset-manager/production.env}"
PLAN="${CAM_PRODUCTION_UI_PLAN:-$ROOT/docs/operations/production-ui-smoke-plan.json}"
OUTPUT_ROOT="${CAM_PRODUCTION_UI_OUTPUT:-$CLIENT/.ui-qa/production-smoke}"
PUBLIC_ONLY="${CAM_PRODUCTION_UI_PUBLIC_ONLY:-0}"
MODE="${CAM_PRODUCTION_UI_MODE:-auto}"
STORAGE_STATE="${CAM_PRODUCTION_UI_STORAGE_STATE:-/etc/creative-asset-manager/production-ui-storage-state.json}"
STORAGE_STATE_EXPLICIT=0
[[ -z "${CAM_PRODUCTION_UI_STORAGE_STATE+x}" ]] || STORAGE_STATE_EXPLICIT=1
EXPECTED_COMMIT="${CAM_PRODUCTION_EXPECTED_COMMIT:-$(git -C "$ROOT" rev-parse HEAD)}"
KEEP_RUNS="${CAM_PRODUCTION_UI_KEEP_RUNS:-5}"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

for command in git node npm python3 stat realpath find; do
  command -v "$command" >/dev/null 2>&1 || die "Required command is unavailable: $command"
done

if [[ -n "${CAM_PRODUCTION_UI_URL:-}" ]]; then
  BASE_URL="${CAM_PRODUCTION_UI_URL%/}"
else
  [[ -f "$ENV_FILE" ]] || die "Production environment file is missing and CAM_PRODUCTION_UI_URL was not set."
  ENV_HELPER="$ROOT/deploy/tools/production_env.py"
  [[ -f "$ENV_HELPER" ]] || die "production_env.py is missing."
  BASE_URL="$(python3 - "$ENV_HELPER" "$ENV_FILE" <<'PY'
from pathlib import Path
import importlib.util
import sys

helper = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("production_env", helper)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)
values = module.parse_environment_file(Path(sys.argv[2]))
print(values["PUBLIC_APP_URL"].rstrip("/"))
PY
)"
fi

python3 - "$BASE_URL" <<'PY'
from urllib.parse import urlparse
import sys
url = urlparse(sys.argv[1])
if url.scheme != "https":
    raise SystemExit("Production UI smoke requires HTTPS.")
if url.hostname in {"127.0.0.1", "localhost", "::1"}:
    raise SystemExit("Production UI smoke refuses loopback/local URLs.")
PY

[[ -f "$PLAN" ]] || die "Production UI smoke plan is missing: $PLAN"
node "$CLIENT/scripts/production-ui-plan-validation.mjs" "$PLAN"
case "$KEEP_RUNS" in
  ''|*[!0-9]*) die "CAM_PRODUCTION_UI_KEEP_RUNS must be a positive integer." ;;
esac
(( KEEP_RUNS >= 1 && KEEP_RUNS <= 20 )) || die "CAM_PRODUCTION_UI_KEEP_RUNS must be between 1 and 20."

case "$MODE" in
  auto|strict|public) ;;
  *) die "CAM_PRODUCTION_UI_MODE must be auto, strict, or public." ;;
esac

if [[ "$PUBLIC_ONLY" == "1" ]]; then
  if [[ -n "${CAM_PRODUCTION_UI_MODE+x}" && "$MODE" != "public" ]]; then
    die "CAM_PRODUCTION_UI_PUBLIC_ONLY=1 conflicts with CAM_PRODUCTION_UI_MODE=$MODE."
  fi
  MODE="public"
fi

EFFECTIVE_MODE="$MODE"
if [[ "$MODE" == "public" ]]; then
  EFFECTIVE_MODE="public"
elif [[ -f "$STORAGE_STATE" ]]; then
  STORAGE_STATE="$(realpath -- "$STORAGE_STATE")"
  ROOT_REAL="$(realpath -- "$ROOT")"
  case "$STORAGE_STATE" in
    "$ROOT_REAL"|"$ROOT_REAL"/*)
      die "Production storage state must live outside the repository." ;;
  esac
  STORAGE_MODE="$(stat -c '%a' "$STORAGE_STATE")"
  if (( (8#$STORAGE_MODE & 077) != 0 )); then
    die "Production storage state must not be readable/writable by group or others; expected mode 600 or stricter."
  fi
  EFFECTIVE_MODE="authenticated"
elif [[ "$MODE" == "strict" || "$STORAGE_STATE_EXPLICIT" == "1" ]]; then
  die "Authenticated Production smoke requires CAM_PRODUCTION_UI_STORAGE_STATE. Use CAM_PRODUCTION_UI_MODE=auto for safe public fallback or public for deliberate public-only coverage."
else
  EFFECTIVE_MODE="public"
  printf 'WARNING: authenticated storage state is unavailable; auto mode is running public-only coverage.\n' >&2
fi

if [[ ! -d "$CLIENT/node_modules/playwright" ]]; then
  printf 'Playwright dependency is missing; installing locked frontend dependencies.\n'
  (cd "$CLIENT" && npm ci)
fi
bash "$ROOT/scripts/cam-install-ui-browser.sh"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ROOT="$OUTPUT_ROOT/$STAMP"
mkdir -p "$RUN_ROOT"

ARGS=(
  --url "$BASE_URL"
  --plan "$PLAN"
  --output "$RUN_ROOT"
  --requested-mode "$MODE"
)
if [[ "$EFFECTIVE_MODE" == "public" ]]; then
  ARGS+=(--public-only)
else
  ARGS+=(--storage-state "$STORAGE_STATE")
fi
if [[ -n "${CAM_PRODUCTION_UI_VIEWPORTS:-}" ]]; then
  ARGS+=(--viewports "$CAM_PRODUCTION_UI_VIEWPORTS")
fi

cd "$CLIENT"
node scripts/production-ui-smoke.mjs "${ARGS[@]}"

REPORT="$(find "$RUN_ROOT" -mindepth 2 -maxdepth 2 -name report.json -type f -print -quit)"
[[ -n "$REPORT" && -f "$REPORT" ]] || die "Production UI smoke report was not produced."

BUILD_COMMIT="$(python3 - "$REPORT" <<'PY'
import json
import sys
report = json.load(open(sys.argv[1], encoding="utf-8"))
print(report["buildInfo"]["build_commit"])
PY
)"

BUILD_COMMIT="$(git -C "$ROOT" rev-parse --verify "${BUILD_COMMIT}^{commit}")" ||
  die "Production build-info references a commit not available in this checkout."
EXPECTED_COMMIT="$(git -C "$ROOT" rev-parse --verify "${EXPECTED_COMMIT}^{commit}")" ||
  die "CAM_PRODUCTION_EXPECTED_COMMIT is invalid."

git -C "$ROOT" merge-base --is-ancestor "$BUILD_COMMIT" "$EXPECTED_COMMIT" ||
  die "Production build-info commit is not an ancestor of the expected deployed commit."

python3 - "$OUTPUT_ROOT" "$KEEP_RUNS" <<'PY'
from pathlib import Path
import shutil
import sys

root = Path(sys.argv[1]).resolve()
keep = int(sys.argv[2])
if root.exists():
    runs = sorted(
        (entry for entry in root.iterdir() if entry.is_dir()),
        key=lambda entry: entry.stat().st_mtime,
        reverse=True,
    )
    for stale in runs[keep:]:
        shutil.rmtree(stale)
PY

printf '\nProduction UI smoke passed.\n'
printf 'Report: %s\n' "$REPORT"
printf 'Coverage: requested=%s effective=%s\n' "$MODE" "$EFFECTIVE_MODE"
printf 'Build provenance: %s <= %s\n' "$BUILD_COMMIT" "$EXPECTED_COMMIT"
printf 'Safety: GET/HEAD/OPTIONS only; non-read requests are blocked by the browser.\n'
