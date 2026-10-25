"""Safety/unit coverage of the read-only G1 restore preflight."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts.cam_pg_g1_preflight_readonly import GIB, evaluate, main


class G1PreflightTests(unittest.TestCase):
    def test_verified_receipt_is_not_restore_pass(self):
        result = evaluate(
            receipt_ok=True, db_bytes=4 * GIB, free_bytes=20 * GIB,
            isolated_cluster_count=1,
        )
        self.assertEqual(result["backup_local_recent_receipt"], "PASS")
        self.assertEqual(result["same_volume_restore_capacity"], "SUFFICIENT_ESTIMATE")
        self.assertEqual(result["isolated_postgresql_cluster"], "PRESENT_UNVERIFIED")
        self.assertEqual(result["isolated_restore_rehearsal"], "NOT_VERIFIED")
        self.assertEqual(result["g1_recovery"], "BLOCKED")
        self.assertEqual(result["production_mutations"], "NONE")

    def test_insufficient_same_volume_capacity_fails_closed(self):
        result = evaluate(
            receipt_ok=True, db_bytes=4 * GIB, free_bytes=9 * GIB,
            isolated_cluster_count=0,
        )
        self.assertEqual(result["same_volume_restore_capacity"], "BLOCKED")
        self.assertEqual(result["isolated_postgresql_cluster"], "NOT_PRESENT")
        self.assertEqual(result["remote_independent_redownload_checksum"], "NOT_VERIFIED")

    def test_missing_receipt_fails_closed(self):
        result = evaluate(
            receipt_ok=False, db_bytes=1 * GIB, free_bytes=15 * GIB,
            isolated_cluster_count=0,
        )
        self.assertEqual(result["backup_local_recent_receipt"], "BLOCKED")
        self.assertEqual(result["g1_recovery"], "BLOCKED")

    def test_invalid_stats_rejected(self):
        with self.assertRaises(ValueError):
            evaluate(receipt_ok=True, db_bytes=0, free_bytes=9 * GIB, isolated_cluster_count=0)

    @patch("scripts.cam_pg_g1_preflight_readonly.os.geteuid", return_value=1000)
    def test_nonroot_never_queries_database(self, _uid):
        with patch("scripts.cam_pg_g1_preflight_readonly._db_bytes") as query:
            self.assertEqual(main(), 2)
            query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
