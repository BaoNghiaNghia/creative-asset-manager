from __future__ import annotations

import argparse
import json

from app.core.database import SessionLocal
from app.modules.ai_operations.pipeline import PipelineOperationsRepository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Pipeline Overview validation")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("pipeline:validate-overview")
    validate.add_argument("--tenant-id", required=True)
    validate.add_argument("--output-json", action="store_true")
    args = parser.parse_args(argv)
    with SessionLocal() as session:
        report = PipelineOperationsRepository(session).validation_report(args.tenant_id)
    if args.output_json:
        print(json.dumps(report, default=str, sort_keys=True))
    else:
        print(f"Tenant: {report['tenant_id']}")
        print(f"Eligible unique assets: {report['eligible_unique_assets']}")
        print(f"Furthest-stage total: {report['furthest_stage_total']}")
        print(f"Unresolved actionable assets: {report['unresolved_actionable_assets']}")
        print(f"Skipped: {report['skipped_assets_by_category']}")
        print(f"Decommissioned sources excluded: {report['decommissioned_source_rows_excluded']}")
        print("Raw attempts versus unique assets:")
        for row in report["raw_attempts_vs_unique_assets"]:
            print(f"  {row['stage']}: {row['unique_assets']} assets, {row['total_attempts']} attempts, {row['failed_attempts']} failed attempts")
        print("Invariant violations: " + (", ".join(report["invariant_violations"]) or "none"))
    return 1 if report["invariant_violations"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
