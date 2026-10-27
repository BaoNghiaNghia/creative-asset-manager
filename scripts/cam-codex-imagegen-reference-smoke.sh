#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
CODEX_HOME="${CAM_CODEX_HOME:-/var/lib/creative-asset-manager/codex}"
SERVICE_USER="${CAM_CODEX_SERVICE_USER:-creative-assets}"
PYTHON="${CAM_IMAGEGEN_SMOKE_PYTHON_BIN:-/opt/creative-asset-manager/current/apps/api/.venv/bin/python}"
SKILL="$CODEX_HOME/skills/concept-short-v1/scripts/make_concepts.py"

[[ "$(id -u)" == "0" ]] || { echo "ERROR: run as root" >&2; exit 2; }
[[ -f "$SKILL" ]] || { echo "ERROR: Concept Short source renderer is missing" >&2; exit 2; }
runuser -u "$SERVICE_USER" -- env -u OPENAI_API_KEY -u CODEX_API_KEY \
  HOME="$CODEX_HOME" CODEX_HOME="$CODEX_HOME" codex login status >/dev/null

JOB="$(mktemp -d "$CODEX_HOME/concept-smoke.XXXXXXXX")"
chown "$SERVICE_USER:$SERVICE_USER" "$JOB"
cleanup() {
  if [[ "${CAM_KEEP_IMAGEGEN_SMOKE:-0}" != "1" ]]; then
    [[ "$JOB" == "$CODEX_HOME"/concept-smoke.* && -d "$JOB" ]] && rm -rf -- "$JOB"
  fi
}
trap cleanup EXIT

install -d -m 0700 -o "$SERVICE_USER" -g "$SERVICE_USER" \
  "$JOB/output/final" "$JOB/working/concept-short" \
  "$JOB/.codex-runtime/generated_images"
chown "$SERVICE_USER:$SERVICE_USER" "$JOB/.codex-runtime"
chmod 0700 "$JOB/.codex-runtime"
for filename in auth.json config.toml managed_config.toml skills rules prompts AGENTS.md plugins; do
  [[ -e "$CODEX_HOME/$filename" ]] || continue
  ln -s "$CODEX_HOME/$filename" "$JOB/.codex-runtime/$filename"
done

runuser -u "$SERVICE_USER" -- "$PYTHON" \
  "$SKILL" "LAS VEGAS" --out "$JOB/working/concept-short"
mapfile -t sources < <(find "$JOB/working/concept-short" -type f -path '*/C1/A_input.png' -print)
[[ "${#sources[@]}" == 1 ]] || { echo "ERROR: source C1 not found"; exit 1; }
SOURCE="${sources[0]}"
SOURCE_REL="$(realpath --relative-to="$JOB" "$SOURCE")"
PROMPT_REL="$(realpath --relative-to="$JOB" "$(dirname "$SOURCE")/A_prompt.txt")"
PROMPT="$(printf '%s\n' \
  'Use the official $imagegen skill and built-in image_gen EDIT tool exactly once.' \
  "First open the local reference with the built-in view_image tool: $SOURCE_REL" \
  "Read the original Concept Short edit instructions verbatim: $PROMPT_REL" \
  'Edit only the viewed original reference with photoreal embroidery stitching.' \
  'Preserve exact glyphs, words, typography layout, thread colors, and white background.' \
  'Never generate from text alone; never use Python/PIL/SVG/artificial stitch filters.' \
  'When the built-in tool returns, copy its raw PNG bytes from CODEX_HOME/generated_images' \
  'into output/final/design_01.png. Do not resize, re-encode or create other finals.' \
  'No API-key-based fallback; if reference edit is unavailable, report failure.' \
  'Do not use a second image-generation call.')"

if ! runuser -u "$SERVICE_USER" -- env -u OPENAI_API_KEY \
    -u OPENAI_ORGANIZATION -u OPENAI_PROJECT -u OPENAI_BASE_URL \
    -u CODEX_API_KEY -u OPENAI_EXECUTOR_API_KEY \
    HOME="$JOB/.codex-runtime" CODEX_HOME="$JOB/.codex-runtime" \
    codex exec --json --ephemeral --skip-git-repo-check --approve-for-me \
    -C "$JOB" "$PROMPT" </dev/null > "$JOB/codex.jsonl"; then
  echo "ERROR: Codex native reference edit failed" >&2
  exit 1
fi

"$PYTHON" - "$SOURCE" "$JOB/output/final/design_01.png" "$JOB/.codex-runtime/generated_images" <<'PY'
from pathlib import Path
from PIL import Image
import hashlib
import re
import sys
reference, final, native = map(Path, sys.argv[1:])
if not final.is_file() or final.is_symlink() or not native.is_dir():
    raise SystemExit("ERROR: no finished native Concept Short PNG")
with Image.open(final) as image:
    image.verify()
    if image.format != "PNG" or min(image.size) < 512:
        raise SystemExit("ERROR: invalid native reference edit PNG")
edited = hashlib.sha256(final.read_bytes()).hexdigest()
if edited == hashlib.sha256(reference.read_bytes()).hexdigest():
    raise SystemExit("ERROR: native edit reused the flat source")
uuid = r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}"
thread_pattern = re.compile(uuid, re.I)
image_pattern = re.compile(rf"exec-{uuid}\.png", re.I)
native_candidates = [
    path for thread in native.iterdir()
    if thread.is_dir() and not thread.is_symlink()
    and thread_pattern.fullmatch(thread.name)
    for path in thread.iterdir()
    if path.is_file() and not path.is_symlink()
    and image_pattern.fullmatch(path.name)
]
native_matches = [
    path for path in native_candidates
    if hashlib.sha256(path.read_bytes()).hexdigest() == edited
]
print(f"NATIVE_EVIDENCE native_candidates={len(native_candidates)} "
      f"matching_finals={len(native_matches)}")
if len(native_matches) != 1:
    raise SystemExit("ERROR: final does not match exactly one native imagegen PNG")
with Image.open(final) as image:
    print(f"REFERENCE_SMOKE_OK width={image.width} height={image.height} "
          f"bytes={final.stat().st_size} native_matches={len(native_matches)}")
PY
