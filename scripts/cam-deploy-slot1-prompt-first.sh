#!/usr/bin/env bash
# Atomically update ONLY the production Codex v3 Slot 1 instructions.
# Does not restart API/workers, modify database/jobs, or touch other skills.
set -Eeuo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
NAME="gatorhats-8869-ecommerce-front-thread-v3"
REL="deploy/codex/skills/$NAME/SKILL.md"
SOURCE="$ROOT/$REL"
RUNTIME_ROOT="${CAM_CODEX_RUNTIME_DIR:-/var/lib/creative-asset-manager/codex}"
TARGET_DIR="$RUNTIME_ROOT/skills/$NAME"
TARGET="$TARGET_DIR/SKILL.md"
BACKUP_DIR="$RUNTIME_ROOT/skill-rollbacks/$NAME"
BACKUP="$BACKUP_DIR/SKILL.md.pre-prompt-first"
BASELINE_REF="fa172730093f419d310e2eeb158030240ac0925c"
MODE="${1:---check}"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "$MODE" == "--check" || "$MODE" == "--apply" || "$MODE" == "--rollback" ]] || die "Expected --check, --apply or --rollback"
[[ "$(id -u)" == "0" ]] || die "Run as root."
command -v flock >/dev/null || die "flock unavailable"
id creative-assets >/dev/null || die "creative-assets service account missing"
[[ -f "$SOURCE" && -f "$TARGET" && ! -L "$TARGET" && ! -L "$TARGET_DIR" ]] || die "Missing or symlinked v3 Skill"
[[ -f "$TARGET_DIR/manifest.json" && -f "$TARGET_DIR/agents/openai.yaml" ]] || die "Runtime v3 Skill is incomplete"
cmp -s "$ROOT/deploy/codex/skills/$NAME/manifest.json" "$TARGET_DIR/manifest.json" || die "Runtime manifest drift: do not overwrite"
cmp -s "$ROOT/deploy/codex/skills/$NAME/agents/openai.yaml" "$TARGET_DIR/agents/openai.yaml" || die "Runtime metadata drift: do not overwrite"

BASELINE_SHA="$(git -C "$ROOT" show "$BASELINE_REF:$REL" | sha256sum | cut -d ' ' -f 1)"
SOURCE_SHA="$(sha256sum "$SOURCE" | cut -d ' ' -f 1)"
TARGET_SHA="$(sha256sum "$TARGET" | cut -d ' ' -f 1)"
[[ "$SOURCE_SHA" != "$BASELINE_SHA" ]] || die "Source Skill prompt is not updated"
if [[ "$TARGET_SHA" != "$BASELINE_SHA" && "$TARGET_SHA" != "$SOURCE_SHA" ]] ; then
  die "Installed Skill differs from both audited baseline and desired prompt; refusing overwrite"
fi
printf 'Slot 1 v3 source=%s installed=%s baseline=%s mode=%s\n' "$SOURCE_SHA" "$TARGET_SHA" "$BASELINE_SHA" "$MODE"
if [[ "$MODE" == "--check" ]]; then
  exit 0
fi

[[ "$(git -C "$ROOT" branch --show-current)" == "main" ]] || die "Only main may deploy"
[[ "$(git -C "$ROOT" rev-parse HEAD)" == "$(git -C "$ROOT" rev-parse origin/main)" ]] || die "Local main must equal origin/main"
[[ -z "$(git -C "$ROOT" status --porcelain --untracked-files=normal)" ]] || die "Release checkout is dirty"

install -d -o creative-assets -g creative-assets -m 0750 "$BACKUP_DIR"
exec 9>"$RUNTIME_ROOT/.slot1-prompt-first.lock"
flock -n 9 || die "Another Slot 1 prompt update is in progress"

if [[ "$MODE" == "--apply" ]]; then
  [[ "$TARGET_SHA" != "$SOURCE_SHA" ]] || { echo "Already activated"; exit 0; }
  "$ROOT/scripts/cam-audit-slot1-prompt-change.sh" --require-clear || die "Outstanding pinned v3 jobs: keep old Skill"
  [[ ! -e "$BACKUP" ]] || cmp -s "$BACKUP" "$TARGET" || die "Backup already exists and does not match installed Skill"
  if [[ ! -e "$BACKUP" ]]; then
    install -m 0640 -o creative-assets -g creative-assets "$TARGET" "$BACKUP"
  fi
  TEMP="$(mktemp "$BACKUP_DIR/.new-skill-XXXXXXXX")"
  trap 'rm -f -- "${TEMP:-}"' EXIT
  install -m 0640 -o creative-assets -g creative-assets "$SOURCE" "$TEMP"
  mv -fT -- "$TEMP" "$TARGET"
  cmp -s "$SOURCE" "$TARGET" || die "Post-activation Skill bytes do not match"
  printf 'ACTIVATED_SLOT1_PROMPT_FIRST sha256=%s\n' "$SOURCE_SHA"
elif [[ "$MODE" == "--rollback" ]]; then
  [[ -f "$BACKUP" && ! -L "$BACKUP" ]] || die "Verified baseline backup is unavailable"
  [[ "$(sha256sum "$BACKUP" | cut -d ' ' -f 1)" == "$BASELINE_SHA" ]] || die "Backup hash does not match baseline"
  "$ROOT/scripts/cam-audit-slot1-prompt-change.sh" --require-clear || die "Outstanding pinned v3 jobs: cannot rollback safely"
  TEMP="$(mktemp "$BACKUP_DIR/.old-skill-XXXXXXXX")"
  trap 'rm -f -- "${TEMP:-}"' EXIT
  install -m 0640 -o creative-assets -g creative-assets "$BACKUP" "$TEMP"
  mv -fT -- "$TEMP" "$TARGET"
  cmp -s "$BACKUP" "$TARGET" || die "Rollback verification failed"
  printf 'ROLLED_BACK_SLOT1_PROMPT_FIRST sha256=%s\n' "$BASELINE_SHA"
fi
