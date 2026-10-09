from __future__ import annotations

import importlib.util
from pathlib import Path


def _module():
    path = Path(__file__).resolve().parents[1] / "cam-clean-orphaned-ui-qa.py"
    spec = importlib.util.spec_from_file_location("orphaned_ui_qa", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_only_old_orphaned_loopback_vite_servers_are_selected():
    report = """
 101 1 3600 node ./apps/client/node_modules/.bin/../../../../node_modules/.pnpm/vite@5.4.14/node_modules/vite/bin/vite.js preview --host 127.0.0.1 --port 42123 --strictPort
 102 1 3600 node ./apps/client/node_modules/.bin/../../../../node_modules/.pnpm/vite@5.4.14/node_modules/vite/bin/vite.js --host 0.0.0.0 --port 42124
 103 321 3600 node ./apps/client/node_modules/.bin/../../../../node_modules/.pnpm/vite@5.4.14/node_modules/vite/bin/vite.js --host 127.0.0.1 --port 42125
 104 1 20 node ./apps/client/node_modules/.bin/../../../../node_modules/.pnpm/vite@5.4.14/node_modules/vite/bin/vite.js --host 127.0.0.1 --port 42126
 105 1 3600 /opt/creative-asset-manager/current/apps/api/.venv/bin/python -m uvicorn app.main:app
 106 1 3600 node /usr/lib/node_modules/codelocal/bin/native/codelocal-linux-x64
    """
    assert _module().orphaned_vite_servers(report=report) == [101]
