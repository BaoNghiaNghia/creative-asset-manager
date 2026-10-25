#!/usr/bin/env python3
"""Plan, but NEVER execute, the exact SQLAlchemy candidate SELECT path.

The same TenantAwareJobClaimer builds its SQL through a capture-only Session.
No job is selected, locked, updated, or claimed. EXPLAIN is WITHOUT ANALYZE.
The settings are synthetic, not extracted from production. Planner estimates
are therefore diagnostics rather than an A/B result for production activation.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections import Counter
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy.dialects import postgresql

from app.core.config import Settings
# Import the processing model before the claimer to avoid a package-init cycle.
from app.modules.processing.model import ProcessingJobModel  # noqa: F401
from app.modules.processing.worker_roles import allowed_job_types_for_role
from app.modules.processing_policy.claim import TenantAwareJobClaimer


class CaptureOnlySession:
    statement = None

    def scalar(self, statement):
        self.statement = statement
        return None

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))


def build_query(role: str, at: datetime | None = None) -> str:
    """Return candidate SELECT built from the unmodified application claimer.

    A placeholder Managed Storage folder exercises the staging-backpressure
    predicate without reading any production secret or tenant-specific value.
    """
    now = at or datetime.now(timezone.utc)
    settings = Settings.model_construct(
        MANAGED_STORAGE_STAGING_MAX_BYTES=1024 * 1024,
        GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID="__plan_only_placeholder__",
        MANAGED_STORAGE_CLEANUP_CRITICAL_PERCENT=90,
    )
    session = CaptureOnlySession()
    claimer = TenantAwareJobClaimer(session, settings)
    eligibility = claimer._eligibility(
        now, allowed_job_types_for_role(role), worker_role=role
    )
    claimer._next_candidate(eligibility, now=now, worker_role=role)
    if session.statement is None:
        raise AssertionError("Claim candidate SQL was not captured")
    query = str(
        session.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    if not query.lstrip().upper().startswith("SELECT "):
        raise AssertionError("Expected read-only SELECT")
    if "FOR UPDATE OF PROCESSING_JOBS SKIP LOCKED" not in query.upper():
        raise AssertionError("SKIP LOCKED claim semantics unexpectedly missing")
    return query


def summarize_plan(payload: list) -> dict[str, object]:
    root = payload[0]["Plan"]
    nodes = Counter()
    indexes = []
    sorts = []

    def walk(node):
        nodes[node["Node Type"]] += 1
        if node.get("Index Name"):
            indexes.append(node["Index Name"])
        if node["Node Type"] in ("Sort", "Incremental Sort"):
            sorts.append(
                {"cost": node.get("Total Cost"), "rows": node.get("Plan Rows")}
            )
        for child in node.get("Plans", []):
            walk(child)

    walk(root)
    return {
        "estimated_total_cost": root.get("Total Cost"),
        "estimated_rows": root.get("Plan Rows"),
        "node_counts": dict(sorted(nodes.items())),
        "indexes": sorted(set(indexes)),
        "sort_nodes": sorts,
    }


def plan_without_execute(query: str, *, timeout: int = 9) -> dict[str, object]:
    env = dict(os.environ)
    env["PGOPTIONS"] = (
        "-c default_transaction_read_only=on "
        "-c statement_timeout=6000 -c lock_timeout=1000"
    )
    completed = subprocess.run(
        [
            "runuser", "-u", "postgres", "--", "psql", "-X", "-q",
            "-A", "-t", "-v", "ON_ERROR_STOP=1", "-d",
            "creative_asset_manager",
        ],
        input="EXPLAIN (FORMAT JSON) " + query + ";\n",
        encoding="utf-8",
        capture_output=True,
        env=env,
        timeout=timeout,
        check=False,
    )
    if completed.returncode:
        # Deliberately omit SQL and database stderr from reports.
        raise RuntimeError(
            "Read-only EXPLAIN failed or exceeded statement timeout"
        )
    return summarize_plan(json.loads(completed.stdout))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--role", default="video-heavy",
        choices=("image", "video", "video-heavy", "video-delivery", "visual"),
    )
    parser.add_argument("--compile-only", action="store_true")
    args = parser.parse_args()
    query = build_query(args.role)
    result = {
        "mode": "READ_ONLY_EXPLAIN_WITHOUT_ANALYZE",
        "role": args.role,
        "sql_compiled": True,
        "query_length": len(query),
        "preserves_skip_locked": True,
        "synthetic_settings": True,
        "production_claim_executed": False,
    }
    if not args.compile_only:
        result["plan"] = plan_without_execute(query)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
