"""Run reviewed PostgreSQL contention suite with the isolated -I interpreter."""
from pathlib import Path
import runpy
import sys

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "apps/api"))
suite = runpy.run_path(str(root / "apps/api/tests/modules/image_orchestrator/postgres_contention.py"))
if __name__ == "__main__":
    raise SystemExit(suite["main"]())
