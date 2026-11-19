"""Safety regression for stale CAM-only, loopback Vite preview cleanup."""
import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/cam-clean-orphaned-ui-qa.py"
spec = importlib.util.spec_from_file_location("cam_orphaned_ui", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class OrphanedViteTests(unittest.TestCase):
    def test_only_detached_old_cam_loopback_vite_servers_match(self):
        report = "\n".join([
            "101 1 450000 node ./.cam-design-modal-20261114/apps/client/node_modules/.bin/vite --host 127.0.0.1 --port 5179 --strictPort",
            "102 1 500000 node /opt/creative-asset-manager/checkout-old/.cam-preview/apps/client/node_modules/.bin/vite preview --host 127.0.0.1 --port 48664 --strictPort",
            "103 1 900000 node /opt/creative-asset-manager/current/apps/client/node_modules/.bin/vite preview --host 127.0.0.1 --port 48664 --strictPort",
            "104 2 900000 node ./.cam-old/apps/client/node_modules/.bin/vite --host 127.0.0.1 --port 48000 --strictPort",
            "105 1 150 node ./.cam-new/apps/client/node_modules/.bin/vite --host 127.0.0.1 --port 48000 --strictPort",
            "106 1 900000 node ./.cam-old/apps/client/node_modules/.bin/vite --host 0.0.0.0 --port 48000 --strictPort",
            "107 1 900000 node ./.cam-old/apps/client/node_modules/.bin/vite --host 127.0.0.1 --port 48000",
            "108 1 900000 node ./.cam-old/apps/client/node_modules/.bin/vite --host 127.0.0.1 --port 48000 --strictPort",
            "not a process",
        ])
        self.assertEqual(module.orphaned_vite_servers(report=report), [101, 102, 108])

    def test_normal_child_and_production_services_are_excluded(self):
        report = "\n".join([
            "10 1 900000 node /opt/creative-asset-manager/current/apps/api/server.js --port 8000 --host 127.0.0.1 --strictPort",
            "11 1 900000 /usr/sbin/nginx: worker process",
            "12 1 900000 node /srv/custom/apps/client/node_modules/.bin/vite preview --host 127.0.0.1 --port 48000 --strictPort",
            "13 1 900000 node ./.cam-old/apps/client/node_modules/.bin/vite preview --host 127.0.0.1 --port 48000 --strictPort",
        ])
        self.assertEqual(module.orphaned_vite_servers(report=report), [13])


if __name__ == "__main__":
    unittest.main()
