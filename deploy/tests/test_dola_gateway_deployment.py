from __future__ import annotations
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UNIT = ROOT / "deploy/systemd/creative-asset-manager-dola-gateway.service"
ENV = ROOT / "deploy/dola-render-gateway.env.example"
CAM_ENV = ROOT / "deploy/production.env.example"
SCRIPT = ROOT / "deploy/tools/prepare_dola_runtime.sh"
LOCK = ROOT / "deploy/dola-render-gateway.requirements.lock"
VIDEO_UNIT = ROOT / "deploy/systemd/creative-asset-manager-video-worker.service"

class DolaGatewayDeploymentTests(unittest.TestCase):
    def test_video_worker_environment_files_are_separate_real_lines(self):
        lines = VIDEO_UNIT.read_text().splitlines()
        production = "EnvironmentFile=/etc/creative-asset-manager/production.env"
        comment = "# Optional Dola bearer/config is scoped to the video worker only."
        optional = "EnvironmentFile=-/etc/creative-asset-manager/video-worker.env"

        self.assertEqual(lines[lines.index(production) + 1], comment)
        self.assertEqual(lines[lines.index(comment) + 1], optional)
        for unit in (ROOT / "deploy/systemd").glob("*.service"):
            for line in unit.read_text().splitlines():
                if line.startswith("EnvironmentFile="):
                    self.assertNotIn(r"\n", line, unit.name)

    def test_video_worker_unit_parses_with_systemd_analyze_when_available(self):
        analyzer = shutil.which("systemd-analyze")
        if analyzer is None:
            self.skipTest("systemd-analyze is unavailable")
        result = subprocess.run([analyzer, "verify", str(VIDEO_UNIT)], capture_output=True, text=True)
        diagnostics = result.stdout + result.stderr
        self.assertNotIn("Assignment outside of section", diagnostics)
        self.assertNotIn("Unknown lvalue", diagnostics)
        self.assertNotIn(r"\n# Optional Dola", diagnostics)

    def test_isolated_loopback_and_default_off(self):
        unit, env, cam, worker, script = [p.read_text() for p in (UNIT, ENV, CAM_ENV, VIDEO_UNIT, SCRIPT)]
        self.assertIn("User=dola-render-gateway", unit)
        self.assertIn("Group=dola-render-gateway", unit)
        self.assertIn("EnvironmentFile=/etc/dola-render-gateway/production.env", unit)
        self.assertIn("DISPLAY=:99", unit)
        self.assertIn("DOLA_GATEWAY_HOST=127.0.0.1", env)
        self.assertIn("DOLA_GATEWAY_PORT=8100", env)
        self.assertIn("DOLA_MAX_CONCURRENCY=1", env)
        self.assertIn("DOLA_INTERNAL_API_KEY=\n", env)
        self.assertIn("/var/lib/dola-render-gateway", env)
        self.assertIn("VIDEO_GENERATION_ENABLED=false", cam)
        self.assertIn("DOLA_RENDER_GATEWAY_ENABLED=false", cam)
        self.assertIn("VIDEO_GENERATION_CANARY_TENANT_IDS=", cam)
        self.assertIn("DOLA_RENDER_GATEWAY_URL=http://127.0.0.1:8100", cam)
        self.assertIn("video-worker.env", worker)
        self.assertNotIn("--no-sandbox", unit + env + script)
        self.assertNotIn("proxy_pass", unit + env + script)

    def test_resource_envelopes_are_explicit_and_bounded(self):
        def values(path):
            return {line.split("=", 1)[0]: line.split("=", 1)[1] for line in path.read_text().splitlines() if "=" in line and not line.lstrip().startswith("#")}
        gateway = values(UNIT)
        xvfb = values(ROOT / "deploy/systemd/creative-asset-manager-dola-xvfb.service")
        self.assertEqual({key: gateway[key] for key in ("MemoryAccounting", "CPUAccounting", "TasksAccounting", "MemoryHigh", "MemoryMax", "CPUQuota", "TasksMax", "OOMPolicy")}, {"MemoryAccounting": "true", "CPUAccounting": "true", "TasksAccounting": "true", "MemoryHigh": "768M", "MemoryMax": "1G", "CPUQuota": "80%", "TasksMax": "512", "OOMPolicy": "stop"})
        self.assertEqual({key: xvfb[key] for key in ("MemoryAccounting", "CPUAccounting", "TasksAccounting", "MemoryHigh", "MemoryMax", "CPUQuota", "TasksMax", "OOMPolicy")}, {"MemoryAccounting": "true", "CPUAccounting": "true", "TasksAccounting": "true", "MemoryHigh": "96M", "MemoryMax": "160M", "CPUQuota": "20%", "TasksMax": "64", "OOMPolicy": "stop"})

    def test_runtime_lock_includes_all_resolved_dependencies(self):
        pinned = {line.split("==", 1)[0].lower() for line in LOCK.read_text().splitlines() if "==" in line}
        expected = {"aiohappyeyeballs", "aiohttp", "aiosignal", "annotated-types", "anyio", "async-timeout", "attrs", "click", "exceptiongroup", "fastapi", "frozenlist", "greenlet", "h11", "idna", "multidict", "patchright", "pillow", "propcache", "pydantic", "pydantic_core", "pyee", "python-dotenv", "starlette", "typing_extensions", "uvicorn", "yarl"}
        self.assertTrue(expected.issubset(pinned))

    def test_script_syntax_and_fake_root_check(self):
        syntax = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp)
            release = fake / "opt/dola-render-gateway/releases/a"
            source = release / "apps/dola_render_gateway"
            (source / "cam_runtime").mkdir(parents=True)
            (source / "upstream/extensions/dola30").mkdir(parents=True)
            (source / "cam_runtime/app.py").write_text("")
            deploy = release / "deploy"; deploy.mkdir()
            (deploy / "dola-render-gateway.requirements.lock").write_text("patchright==1\n")
            current = fake / "opt/dola-render-gateway/current"; current.parent.mkdir(parents=True, exist_ok=True)
            current.symlink_to(release)
            env = fake / "etc/dola-render-gateway/production.env"; env.parent.mkdir(parents=True)
            env.write_text("DOLA_GATEWAY_HOST=127.0.0.1\nDOLA_GATEWAY_PORT=8100\nDOLA_MAX_CONCURRENCY=1\n")
            result = subprocess.run(["bash", str(SCRIPT), "--check", "--root", str(fake)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("check passed", result.stdout)

    def test_no_public_nginx_dola_route(self):
        text = "\n".join(p.read_text(errors="ignore") for p in (ROOT / "infrastructure").rglob("*") if p.is_file())
        self.assertNotIn("proxy_pass http://127.0.0.1:8100", text)
        self.assertNotIn("/dola", text.lower())

if __name__ == "__main__":
    unittest.main()