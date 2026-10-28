#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${CAM_QA_PYTHON:-python3}"
cd "$ROOT/apps/api"
exec "$PYTHON" -m pytest -q \
  tests/modules/realistic_review_ugc/test_slot1_embroidery.py \
  tests/modules/realistic_review_ugc/test_slot1_skill_repin.py \
  tests/modules/realistic_review_ugc/test_ecommerce_pipeline.py \
  tests/modules/realistic_review_ugc/test_ecommerce_registry_discovery.py \
  tests/modules/realistic_review_ugc/test_ecommerce_worker.py
