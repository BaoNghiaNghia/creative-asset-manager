from __future__ import annotations

import argparse
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1] / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.modules.visual_search.model_spec import SIGLIP2_MODEL, SIGLIP2_REVISION


_REQUIRED_FILES = (
    "config.json",
    "model.safetensors",
    "preprocessor_config.json",
    "tokenizer.json",
    "tokenizer.model",
    "tokenizer_config.json",
)


def provision_model(root: Path) -> Path:
    """Download the exact approved SigLIP2 snapshot outside service startup."""
    from huggingface_hub import snapshot_download

    target = root / SIGLIP2_REVISION
    target.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=SIGLIP2_MODEL,
        revision=SIGLIP2_REVISION,
        local_dir=target,
    )
    missing = [name for name in _REQUIRED_FILES if not (target / name).is_file()]
    if missing:
        raise RuntimeError(
            "Pinned SigLIP2 snapshot is incomplete: " + ", ".join(sorted(missing))
        )
    return target


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Provision the pinned CAM SigLIP2 visual-encoder snapshot."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("/var/lib/creative-asset-manager/models/siglip2"),
        help="Directory that will contain the immutable revision subdirectory.",
    )
    args = parser.parse_args()
    target = provision_model(args.root)
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
