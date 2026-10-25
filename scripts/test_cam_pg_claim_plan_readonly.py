"""Unit guards for read-only SQLAlchemy claim EXPLAIN diagnostic."""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from scripts.cam_pg_claim_plan_readonly import build_query, plan_without_execute

STAMP = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


class ClaimExplainSafetyTest(unittest.TestCase):
    def test_uses_real_application_claimer_without_claiming(self):
        for role in ("image", "video-heavy", "visual"):
            with self.subTest(role=role):
                query = build_query(role, STAMP)
                self.assertTrue(query.startswith("SELECT "))
                self.assertIn("FOR UPDATE OF processing_jobs SKIP LOCKED", query)
                self.assertIn("ORDER BY", query)
                self.assertIn("tenant_processing_policies", query)
                self.assertIn("tenant_provider_policies", query)
                self.assertNotIn("DELETE FROM", query)
                self.assertNotIn("UPDATE processing_jobs SET", query)

    @patch("scripts.cam_pg_claim_plan_readonly.subprocess.run")
    def test_explain_has_no_analyze_or_write_and_readonly_env(self, run):
        run.return_value.returncode = 0
        run.return_value.stdout = json.dumps([{
            "Plan": {
                "Node Type": "Limit", "Total Cost": 20, "Plan Rows": 1,
                "Plans": [{"Node Type": "Sort", "Total Cost": 19, "Plan Rows": 5,
                           "Plans": [{"Node Type": "Index Scan", "Index Name": "idx"}]}]
            }
        }])
        query = build_query("visual", STAMP)
        result = plan_without_execute(query)
        self.assertEqual(result["estimated_total_cost"], 20)
        self.assertEqual(result["node_counts"]["Sort"], 1)
        self.assertEqual(result["indexes"], ["idx"])
        call = run.call_args
        self.assertTrue(call.kwargs["input"].startswith("EXPLAIN (FORMAT JSON) SELECT "))
        self.assertNotIn("ANALYZE", call.kwargs["input"])
        self.assertIn("default_transaction_read_only=on", call.kwargs["env"]["PGOPTIONS"])
        self.assertEqual(call.args[0][:3], ["runuser", "-u", "postgres"])


if __name__ == "__main__":
    unittest.main()
