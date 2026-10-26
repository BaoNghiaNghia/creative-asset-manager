#!/usr/bin/env python3
"""Pin two Natural/Black and Natural/Navy fronts to the new 12-hat silhouette.

Dry-run by default; --apply atomically installs exactly these two immutable
front images, retaining the original supplier images and every old output.
It does not queue any AI jobs or update the full 4-angle stock manifest.
"""
import argparse
import json

from app.core.config import get_settings
from app.modules.realistic_review_ugc.slot7_new_hat_fronts import (
    reconcile_new_hat_fronts,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    options = parser.parse_args()
    result = reconcile_new_hat_fronts(get_settings(), apply=options.apply)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
