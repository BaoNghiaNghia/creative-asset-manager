from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[3]


def test_api_visual_search_imports_do_not_require_ml_runtime(tmp_path: Path) -> None:
    """The API must import without torch, transformers, or sentencepiece installed."""
    script = """
import builtins
blocked = {\"torch\", \"transformers\", \"sentencepiece\"}
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.', 1)[0] in blocked:
        raise AssertionError(f\"heavy ML import attempted: {name}\")
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import app.main
import app.modules.visual_search.router
import app.modules.visual_search.encoder_client
import app.modules.visual_search.lifecycle
"""
    environment = os.environ | {
        "PYTHONPATH": str(API_ROOT),
        "DATABASE_BACKUP_STAGING_DIRECTORY": str(tmp_path),
    }
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
