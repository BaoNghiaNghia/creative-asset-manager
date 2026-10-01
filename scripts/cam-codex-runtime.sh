#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
CODEX_HOME="${CAM_CODEX_HOME:-/var/lib/creative-asset-manager/codex}"
SERVICE_USER="${CAM_CODEX_SERVICE_USER:-creative-assets}"
SKILLS_SOURCE="$ROOT/deploy/codex/skills"
SMOKE_WORKSPACE="$CODEX_HOME/smoke-workspace"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_root() {
  [[ "$(id -u)" == "0" ]] || die "Run this action as root."
}

prepare() {
  require_root
  command -v codex >/dev/null || die "Codex CLI is not installed."
  id "$SERVICE_USER" >/dev/null 2>&1 || die "Service user does not exist: $SERVICE_USER"
  [[ -d "$SKILLS_SOURCE" ]] || die "Project Codex skills are missing."

  install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$CODEX_HOME"
  install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$CODEX_HOME/skills"

  while IFS= read -r -d '' skill_dir; do
    skill_name="$(basename -- "$skill_dir")"
    [[ "$skill_name" =~ ^[a-z0-9][a-z0-9-]{0,63}$ ]] \
      || die "Invalid skill directory: $skill_name"
    [[ -f "$skill_dir/SKILL.md" ]] || die "Missing SKILL.md: $skill_name"
    destination="$CODEX_HOME/skills/$skill_name"
    install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$destination"
    rsync -a --delete --chown="$SERVICE_USER:$SERVICE_USER" \
      "$skill_dir/" "$destination/"
  done < <(
    find "$SKILLS_SOURCE" -mindepth 1 -maxdepth 1 -type d -print0 | sort -z
  )

  printf 'CODEX_HOME=%s\n' "$CODEX_HOME"
  find "$CODEX_HOME/skills" -mindepth 2 -maxdepth 2 -name SKILL.md -printf '%P\n' | sort
}

codex_as_service_user() {
  runuser -u "$SERVICE_USER" -- \
    env \
      -u OPENAI_API_KEY \
      -u OPENAI_ORGANIZATION \
      -u OPENAI_PROJECT \
      -u OPENAI_BASE_URL \
      -u CODEX_API_KEY \
      -u OPENAI_EXECUTOR_API_KEY \
      HOME="$CODEX_HOME" \
      CODEX_HOME="$CODEX_HOME" \
      "$@"
}

login() {
  prepare >/dev/null
  codex_as_service_user codex login --device-auth
}

status() {
  require_root
  codex_as_service_user codex login status
}

smoke() {
  prepare >/dev/null
  codex_as_service_user codex login status >/dev/null \
    || die "Codex is not authenticated for $SERVICE_USER."

  rm -rf "$SMOKE_WORKSPACE"
  install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_USER" \
    "$SMOKE_WORKSPACE/output"

  codex_as_service_user \
    codex exec \
      --json \
      --ephemeral \
      --skip-git-repo-check \
      --sandbox workspace-write \
      --approve-for-me \
      -C "$SMOKE_WORKSPACE" \
      'Use $cam-imagegen-smoke and $imagegen. Generate exactly one image and save it to output/final.png. Do not use an API-key-backed fallback.'

  output="$SMOKE_WORKSPACE/output/final.png"
  [[ -s "$output" ]] || die "Smoke test did not create output/final.png."

  python_bin="/opt/creative-asset-manager/current/apps/api/.venv/bin/python"
  [[ -x "$python_bin" ]] || die "Production Python runtime is unavailable."
  "$python_bin" - "$output" <<'PY'
from pathlib import Path
import sys
from PIL import Image

path = Path(sys.argv[1])
with Image.open(path) as image:
    image.load()
    if image.format != "PNG":
        raise SystemExit("output is not PNG")
    width, height = image.size
    if width < 64 or height < 64:
        raise SystemExit("output dimensions are unexpectedly small")
print(f"SMOKE_OK path={path} width={width} height={height} bytes={path.stat().st_size}")
PY
}

case "${1:-}" in
  prepare) prepare ;;
  login) login ;;
  status) status ;;
  smoke) smoke ;;
  *)
    cat >&2 <<'USAGE'
Usage: sudo scripts/cam-codex-runtime.sh {prepare|login|status|smoke}
USAGE
    exit 2
    ;;
esac
