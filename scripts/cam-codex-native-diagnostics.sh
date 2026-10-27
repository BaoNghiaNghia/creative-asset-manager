#!/usr/bin/env bash
set -Eeuo pipefail
CODEX_HOME="${CAM_CODEX_HOME:-/var/lib/creative-asset-manager/codex}"
PYTHON="${CAM_IMAGEGEN_SMOKE_PYTHON_BIN:-/opt/creative-asset-manager/current/apps/api/.venv/bin/python}"
IDENTIFIER="${1:-}"
if [[ ! "$IDENTIFIER" =~ ^concept-smoke\.[a-zA-Z0-9]{8}$ ]]; then
  echo "ERROR: supply exact previously created concept smoke identifier" >&2
  exit 2
fi
JOB="$CODEX_HOME/$IDENTIFIER"
[[ -d "$JOB" && ! -L "$JOB" ]] || { echo "ERROR: smoke folder unavailable" >&2; exit 2; }
"$PYTHON" - "$JOB" <<'PY'
import hashlib, json, sys
from collections import Counter
from pathlib import Path
from PIL import Image

job = Path(sys.argv[1])
cache = job / ".codex-runtime" / "generated_images"
final = job / "output" / "final" / "design_01.png"
final_digest = hashlib.sha256(final.read_bytes()).hexdigest() if final.is_file() else None
matched = 0
print("FINAL", "present" if final_digest else "absent", "sha_prefix", str(final_digest)[:16])
if not cache.is_dir() or cache.is_symlink():
    print("CACHE unsafe_or_missing")
else:
    for path in cache.rglob("*"):
        if path.is_dir() and not path.is_symlink():
            print("NATIVE_DIR", path.relative_to(cache).as_posix())
        if path.is_file() and not path.is_symlink():
            signature = hashlib.sha256(path.read_bytes()).hexdigest()
            try:
                with Image.open(path) as image:
                    shape = f"{image.format} {image.width}x{image.height}"
            except Exception:
                shape = "unknown"
            print("NATIVE", path.name, path.stat().st_size,
                  shape, signature[:16], "MATCH" if signature == final_digest else "DIFFERENT")
            if signature == final_digest and shape.startswith("PNG "):
                matched += 1
log = job / "codex.jsonl"
counts = Counter()
if log.is_file():
    for line in log.read_text(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        item = event.get("item") or {}
        counts[(event.get("type"),item.get("type"))] += 1
        if (item.get("type") == "command_execution"
                and event.get("type") == "item.completed"
                and "generated_images" in str(item.get("command", ""))):
            command = str(item.get("command", ""))
            print("COPY_COMMAND_CONTAINS_NATIVE_PATH",
                  "cp " in command, "imagegen_path_mentions", command.count("generated_images"))
print("EVENT_COUNTS", str(dict(counts))[:550])
if matched != 1:
    raise SystemExit("REFERENCE_SMOKE_PROVENANCE_FAILED")
print("REFERENCE_SMOKE_PROVENANCE_OK matched=1")
PY
