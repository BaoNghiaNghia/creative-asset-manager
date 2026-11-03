#!/usr/bin/env python3
"""Read-only, redacted G3 reference/DNA/queue inventory for operator preflight.

No provider calls, job enqueues, writes, image access or private identifiers.
Read-only PostgreSQL transaction is enforced by the database itself.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session


def audit(session: Session, *, tenant_id: str) -> dict:
    """Run only aggregated metadata queries within an existing read-only session."""
    from app.modules.realistic_review_ugc.typography_analysis_queue import VERIFIED_RIGHTS
    from app.modules.realistic_review_ugc.typography_dna_review import effective_reviewed_dna
    from app.modules.realistic_review_ugc.typography_model import (
        RrugcTypographyReferenceModel as Ref,
        RrugcTypographyStyleDnaModel as Dna,
        RrugcTypographyAnalysisJobModel as Job,
    )

    references = session.scalars(
        select(Ref).where(Ref.tenant_id == tenant_id).order_by(Ref.id)
    ).all()
    rights = Counter(r.source_rights_status for r in references)
    editorial = Counter(r.editorial_status for r in references)
    candidate_ids = {
        r.id for r in references if r.source_rights_status in VERIFIED_RIGHTS
        and r.editorial_status == "approved" and len(r.content_hash or "") == 64
    }
    dna_rows = session.scalars(
        select(Dna).where(Dna.tenant_id == tenant_id)
    ).all()
    refs_by_id = {r.id: r for r in references}
    accepted = set()
    raw_ready = set()
    for dna in dna_rows:
        ref = refs_by_id.get(dna.reference_id)
        if ref is None:
            continue
        if dna.image_hash == ref.content_hash and dna.schema_version == "typography-dna/v1":
            raw_ready.add(ref.id)
        if ref.id in candidate_ids and effective_reviewed_dna(ref, dna) is not None:
            accepted.add(ref.id)
    status = Counter(session.scalars(
        select(Job.status).where(Job.tenant_id == tenant_id)
    ).all())
    return {
        "status": "read_only",
        "reference_count": len(references),
        "rights_verified_metadata_count": sum(rights[s] for s in VERIFIED_RIGHTS),
        "rights_unknown_or_blocked_count": rights["unknown"] + rights["blocked"],
        "editorial_approved_count": editorial["approved"],
        "candidate_rights_and_editorial_count": len(candidate_ids),
        "dna_current_count": len(raw_ready),
        "dna_human_accepted_count": len(accepted),
        "job_counts": {status_name: status[status_name] for status_name in (
            "queued", "running", "completed", "failed", "needs_review"
        )},
        "eight_reference_metadata_candidate_pool": len(candidate_ids) >= 8,
        "rights_documents_independently_verified": False,
        "provider_calls": 0,
        "writes": 0,
        "warning": "Rights metadata is not legal evidence; human review remains mandatory.",
    }


def aggregate_inventory(session: Session) -> dict:
    """Aggregate across tenants without exposing or persisting their identifiers."""
    from app.modules.realistic_review_ugc.typography_model import (
        RrugcTypographyReferenceModel as Ref,
    )
    tenant_ids = session.scalars(select(Ref.tenant_id).distinct()).all()
    reports = [audit(session, tenant_id=tenant_id) for tenant_id in tenant_ids]
    totals = {
        key: sum(report[key] for report in reports)
        for key in (
            "reference_count", "rights_verified_metadata_count",
            "rights_unknown_or_blocked_count", "editorial_approved_count",
            "candidate_rights_and_editorial_count", "dna_current_count",
            "dna_human_accepted_count",
        )
    }
    job_names = ("queued", "running", "completed", "failed", "needs_review")
    return {
        "status": "read_only",
        "tenant_count": len(tenant_ids),
        **totals,
        "job_counts": {
            name: sum(report["job_counts"][name] for report in reports)
            for name in job_names
        },
        "tenants_with_eight_metadata_candidates": sum(
            report["eight_reference_metadata_candidate_pool"] for report in reports
        ),
        "rights_documents_independently_verified": False,
        "provider_calls": 0,
        "writes": 0,
        "warning": "Rights metadata is not legal evidence; human review remains mandatory.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only redacted G3 pilot metadata inventory")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--tenant-id", help="Authorized tenant ID (not printed)")
    target.add_argument("--all-tenants", action="store_true",
                        help="Operator-only redacted aggregate; never prints tenant IDs")
    parser.add_argument("--env-file", default="/etc/creative-asset-manager/production.env")
    args = parser.parse_args(argv)
    if args.tenant_id is not None and not args.tenant_id.strip():
        print(json.dumps({"status": "invalid_tenant"}))
        return 2
    try:
        from dotenv import load_dotenv
        load_dotenv(args.env_file, override=True)
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))
        from app.core.config import get_settings
        from app.core.database import create_database_engine
        settings = get_settings()
        if not str(settings.DATABASE_URL or "").startswith("postgresql"):
            raise RuntimeError("postgresql_required")
        engine = create_database_engine(settings)
        try:
            with Session(engine) as session:
                session.execute(text("SET TRANSACTION READ ONLY"))
                result = (
                    aggregate_inventory(session) if args.all_tenants else
                    audit(session, tenant_id=args.tenant_id)
                )
                session.rollback()
        finally:
            engine.dispose()
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        # Never print exception strings: driver/config exceptions can contain secrets.
        print(json.dumps({"status": "audit_unavailable", "error_type": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
