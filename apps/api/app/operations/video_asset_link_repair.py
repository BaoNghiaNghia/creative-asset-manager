from __future__ import annotations

import argparse
import json

from app.core.database import SessionLocal
from app.modules.assets.video_link_repair import repair_video_asset_links


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description=(
            "Repair missing Asset/SourceAsset links for videos when the source "
            "registry already has a provider SHA-256 checksum."
        )
    )
    value.add_argument("--tenant-id")
    value.add_argument("--limit", type=int, default=1000)
    value.add_argument(
        "--execute",
        action="store_true",
        help="Persist repairs. Without this flag the command is read-only.",
    )
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    with SessionLocal() as session:
        result = repair_video_asset_links(
            session,
            tenant_id=args.tenant_id,
            limit=args.limit,
            execute=args.execute,
        )
    print(json.dumps(result.to_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
