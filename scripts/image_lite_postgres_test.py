"""Run one isolated Muse Lite contention test per disposable PostgreSQL cluster."""
from pathlib import Path
import os
import sys
import pytest

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/"apps/api"))
os.environ["CAM_LITE_TEST_ISOLATED"]="1"
case=os.environ.get("CAM_LITE_PG_CASE","concurrent_claim_postgres")
assert case in ("concurrent_claim_postgres","p0_concurrent_retry_postgres")
raise SystemExit(pytest.main(["-q",str(root/"apps/api/tests/modules/image_orchestrator/test_lite_api.py"),
  "-k",case,"--disable-warnings"]))
