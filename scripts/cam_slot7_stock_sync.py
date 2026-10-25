#!/usr/bin/env python3
"""Preview or import four verified high-resolution Valucap 8869 Slot 7 front photos.

Never modifies the shared 3-angle stock catalog or completed outputs. Existing
immutable revisions remain available for inspection and controlled rollback.
"""
from __future__ import annotations

import argparse
import json

from app.core.config import get_settings
from app.modules.realistic_review_ugc.slot7_front_stock import (
    SUPPLIER_VARIANTS, read_slot7_front, sync_supplier_fronts,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Write immutable checked front images and atomic manifest")
    args = parser.parse_args()
    settings = get_settings()
    report = sync_supplier_fronts(settings, apply=args.apply)
    if args.apply:
        for color in SUPPLIER_VARIANTS:
            imported = read_slot7_front(settings, color)
            if imported is None or len(imported[1]) != 64:
                raise RuntimeError("Post-sync verification failed: " + color)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
