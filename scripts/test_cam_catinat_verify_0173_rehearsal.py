"""Regression tests for repeated, non-mutating Catinat PostgreSQL audit."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cam_catinat_verify_0173_rehearsal import (
    CloneMigrationBlocked, _read_private, verify_previous_proof,
)


class VerifyPreviousAuditTest(unittest.TestCase):
    def test_first_audit_requires_no_existing_proof(self):
        verify_previous_proof(None, {"ISOLATED_0173_AUDIT": "PASS"})

    def test_repeated_audit_ignores_timestamp_only(self):
        previous = {"AUDITED_AT_UTC": "2026-10-21T01:00Z", "ALEMBIC_CURRENT": "0173_catinat_branding_merge", "SHEETS_WRITES": 0}
        current = dict(previous, AUDITED_AT_UTC="2026-10-21T03:00Z")
        verify_previous_proof(previous, current)

    def test_changed_revision_is_rejected(self):
        a = {"AUDITED_AT_UTC": "a", "ALEMBIC_CURRENT": "0173_catinat_branding_merge"}
        with self.assertRaises(CloneMigrationBlocked):
            verify_previous_proof(a, dict(a, ALEMBIC_CURRENT="0172_catinat_transfers"))

    def test_omitted_or_extra_evidence_is_rejected(self):
        with self.assertRaises(CloneMigrationBlocked):
            verify_previous_proof({"ALEMBIC_CURRENT": "0173"}, {"ALEMBIC_CURRENT": "0173", "SHEETS_WRITES": 0})

    def test_existing_proof_is_private_and_not_rewritten(self):
        with tempfile.TemporaryDirectory() as root:
            proof = Path(root) / "proof.json"
            proof.write_text(json.dumps({"ISOLATED_0173_AUDIT": "PASS"}), encoding="utf-8")
            os.chmod(proof, 0o600)
            before = proof.read_bytes()
            self.assertEqual(_read_private(proof)["ISOLATED_0173_AUDIT"], "PASS")
            self.assertEqual(proof.read_bytes(), before)
            os.chmod(proof, 0o644)
            with self.assertRaises(CloneMigrationBlocked):
                _read_private(proof)


if __name__ == "__main__":
    unittest.main()