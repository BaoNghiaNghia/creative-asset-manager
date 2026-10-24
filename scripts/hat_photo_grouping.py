#!/usr/bin/env python3
"""Read-only Valucap 8869 photo ZIP audit; no database/stock writes."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from app.modules.realistic_review_ugc.hat_stock_sync import (  # noqa: E402
    MAX_ZIP_BYTES, HatStockSyncError, scan_archive,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Preview ZIP color/angle group matches")
    parser.add_argument("zip", type=Path)
    args = parser.parse_args()
    if not args.zip.is_file() or args.zip.stat().st_size > MAX_ZIP_BYTES:
        parser.error("Source must be a ZIP file smaller than 120 MB")
    try:
        report, _ = scan_archive(args.zip.read_bytes())
    except HatStockSyncError as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2))
    return 0 if report["ready_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
