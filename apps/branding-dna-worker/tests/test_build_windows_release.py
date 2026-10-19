"""Safety and determinism regression for Windows portable release builder."""
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile

BUILD_SCRIPT = Path(__file__).resolve().parents[1] / "scripts/build_windows_release.py"
spec = importlib.util.spec_from_file_location("bdna_release_builder", BUILD_SCRIPT)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class ReleaseBuilderTests(unittest.TestCase):
    def test_version_pin_sources_and_no_local_secrets(self):
        inputs = builder.release_inputs()
        self.assertIn("START_BROWSER_PILOT.cmd", inputs)
        self.assertIn("START_CAM_AGENT.cmd", inputs)
        self.assertIn(b"START_CAM_AGENT.cmd", inputs["START_BROWSER_PILOT.cmd"])
        self.assertNotIn(b"pilot.local.json", inputs["START_BROWSER_PILOT.cmd"])
        self.assertIn("INSTALL_TOOL.cmd", inputs)
        self.assertIn("QUICK_START_VI.txt", inputs)
        self.assertIn("package-lock.json", inputs)
        self.assertIn("docs/BROWSER_AGENT_PILOT.md", inputs)
        self.assertNotIn("config/pilot.local.json", inputs)
        self.assertNotIn("config/approval.local.json", inputs)
        self.assertFalse(any("node_modules" in name or ".env" in name or "worker-outbox" in name for name in inputs))
        self.assertEqual(json.loads(inputs["package.json"])["version"], builder.VERSION)
        self.assertIn(b"\r\n", inputs["INSTALL_TOOL.cmd"])

    def test_build_is_deterministic_and_manifest_verified(self):
        with TemporaryDirectory() as directory:
            output=Path(directory)/"build.zip"
            builder.build(output)
            first=output.read_bytes()
            report=builder.verify(output)
            self.assertEqual(report["version"],"0.2.6")
            self.assertFalse(report["production_scan_enabled"])
            self.assertFalse(report["automatic_scan_on_launch"])
            self.assertFalse(report["marketplace_api_used"])
            self.assertFalse(report["external_scraping_service_used"])
            self.assertFalse(report["binary_bundled"])
            self.assertEqual(report["file_count"],len(builder.release_inputs()))
            checksum=output.with_suffix(".zip.sha256").read_text()
            self.assertTrue(checksum.startswith(builder.sha(first)))
            builder.build(output)
            self.assertEqual(first,output.read_bytes(),"Release should be byte-for-byte reproducible")

    def test_tampered_archive_is_rejected(self):
        with TemporaryDirectory() as directory:
            original=Path(directory)/"original.zip"
            modified=Path(directory)/"tampered.zip"
            builder.build(original)
            with zipfile.ZipFile(original) as src, zipfile.ZipFile(modified,"w") as out:
                for member in src.infolist():
                    blob=src.read(member.filename)
                    if member.filename.endswith("/README.md"):
                        blob+=b"\nFAKE"
                    out.writestr(member,blob)
            with self.assertRaisesRegex(ValueError,"archive_file_hash_mismatch"):
                builder.verify(modified)


if __name__=="__main__":
    unittest.main()
