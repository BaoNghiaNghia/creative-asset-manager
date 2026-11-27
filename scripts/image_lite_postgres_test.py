"""Run only the concurrent Lite claim test on the disposable P2C PostgreSQL."""
from pathlib import Path
import os
import sys
import pytest
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/"apps/api"))
os.environ["CAM_LITE_TEST_ISOLATED"]="1"
raise SystemExit(pytest.main(["-q",str(root/"apps/api/tests/modules/image_orchestrator/test_lite_api.py"),
  "-k","concurrent_claim_postgres","--disable-warnings"]))
