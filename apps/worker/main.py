#!/usr/bin/env python3
from __future__ import annotations

import logging
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1] / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.modules.processing.bootstrap import run_default_worker  # noqa: E402


def main() -> int:
    try:
        return run_default_worker()
    except Exception as exc:
        logging.basicConfig(level=logging.ERROR)
        logging.getLogger("cam.worker").critical(
            "worker_configuration_failed: %s", exc, exc_info=True
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
