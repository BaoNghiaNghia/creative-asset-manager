"""Conservative one-off Stage 1 reset for the three screenshot-confirmed keywords.

The active logical jobs are removed, so Stage 1 reports Not run and lets users
Generate again. Immutable output versions, processing history and managed Drive
files are deliberately retained. Read-only by default; reject ambiguous
keywords, non-terminal processing and unexpected image counts.
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.modules.processing.model import ProcessingJobModel
from app.modules.realistic_review_ugc.model import (
    RrugcImageOutputVersionModel, RrugcKeywordImageJobModel, RrugcKeywordVolumeModel,
)

TARGETS: tuple[tuple[str, int], ...] = (
    ("beach please", 140),
    ("BEACH Life", 50),
    ("Dear Sunday", 10),
)
ACTIVE_PROCESSING = frozenset({"pending", "processing", "retry", "queued", "running", "claimed"})


@dataclass(frozen=True)
class ResetCandidate:
    keyword: str
    search_volume: int
    tenant_id: str
    keyword_id: str
    job_id: str
    processing_job_id: str
    original_status: str
    version_count: int
    remote_file_id: str


def inspect_candidates(session: Session, *, lock: bool = False) -> list[ResetCandidate]:
    """Require each pictured keyword to match one used/finished job only."""
    results: list[ResetCandidate] = []
    for label, volume in TARGETS:
        stmt = (
            select(RrugcKeywordVolumeModel, RrugcKeywordImageJobModel)
            .join(
                RrugcKeywordImageJobModel,
                (RrugcKeywordImageJobModel.keyword_id == RrugcKeywordVolumeModel.id)
                & (RrugcKeywordImageJobModel.tenant_id == RrugcKeywordVolumeModel.tenant_id),
            )
            .where(
                func.lower(func.trim(RrugcKeywordVolumeModel.keyword)) == label.lower(),
                RrugcKeywordVolumeModel.search_volume == volume,
                RrugcKeywordVolumeModel.picked.is_(True),
            )
        )
        if lock:
            stmt = stmt.with_for_update(of=RrugcKeywordImageJobModel)
        matches = list(session.execute(stmt))
        if len(matches) != 1:
            raise RuntimeError(
                f"{label} (volume={volume}): expected exactly one used Stage 1 job, found {len(matches)}"
            )
        keyword, job = matches[0]
        if job.status != "completed" or not job.output_remote_file_id or not job.processing_job_id:
            raise RuntimeError(f"{label}: job not completed or output/processing pointer missing")
        processing = list(session.scalars(
            select(ProcessingJobModel).where(
                ProcessingJobModel.tenant_id == job.tenant_id,
                ProcessingJobModel.entity_type == "rrugc_keyword_image_job",
                ProcessingJobModel.entity_id == job.id,
            )
        ))
        if not processing or any(p.status in ACTIVE_PROCESSING for p in processing):
            raise RuntimeError(f"{label}: missing processing history or processing still active")
        current = next((p for p in processing if p.id == job.processing_job_id), None)
        if current is None or current.status != "completed":
            raise RuntimeError(f"{label}: latest processing job is not completed")
        versions = list(session.scalars(
            select(RrugcImageOutputVersionModel).where(
                RrugcImageOutputVersionModel.tenant_id == job.tenant_id,
                RrugcImageOutputVersionModel.stage == "stage1",
                RrugcImageOutputVersionModel.job_id == job.id,
            )
        ))
        if len(versions) > 1 or (versions and versions[0].remote_file_id != job.output_remote_file_id):
            raise RuntimeError(f"{label}: expected zero or one matching saved image, found {len(versions)}")
        # Legacy jobs can have one completed image without a version row.
        # Preserve the image pointer by creating its immutable version on apply.
        results.append(ResetCandidate(
            keyword=keyword.keyword,
            search_volume=volume,
            tenant_id=job.tenant_id,
            keyword_id=keyword.id,
            job_id=job.id,
            processing_job_id=job.processing_job_id,
            original_status=job.status,
            version_count=len(versions),
            remote_file_id=job.output_remote_file_id,
        ))
    if len({c.tenant_id for c in results}) != 1:
        raise RuntimeError("Target keywords belong to different tenants; refusing multi-tenant reset")
    return results


def reset_candidates(session: Session, *, expected_job_ids: set[str]) -> list[ResetCandidate]:
    """Re-check while locked, then remove active linkage only in one transaction."""
    candidates = inspect_candidates(session, lock=True)
    current_ids = {c.job_id for c in candidates}
    if len(expected_job_ids) != len(TARGETS) or current_ids != expected_job_ids:
        raise RuntimeError("Job IDs changed since dry-run; refusing reset")
    for item in candidates:
        job = session.get(RrugcKeywordImageJobModel, item.job_id)
        if job is None:
            raise RuntimeError("Job disappeared during reset")
        if item.version_count == 0:
            # Some production outputs predate version history. Materialize the
            # missing immutable record before detaching the logical job.
            session.add(RrugcImageOutputVersionModel(
                tenant_id=item.tenant_id, stage="stage1", job_id=item.job_id,
                version=1, processing_job_id=item.processing_job_id,
                remote_file_id=item.remote_file_id,
                content_type=job.output_content_type,
                size_bytes=job.output_size_bytes,
                width=job.output_width, height=job.output_height,
                created_at=job.completed_at or job.updated_at,
            ))
            session.flush()
        session.delete(job)
    session.flush()
    return candidates


def verify_reset(session: Session, manifest: dict) -> list[str]:
    """Read-only check of the exact three rows, preserved versions and logs."""
    entries = manifest.get("candidates", [])
    if len(entries) != 3 or {(item["keyword"].lower(), item["search_volume"]) for item in entries} != {
        (name.lower(), volume) for name, volume in TARGETS
    }:
        raise RuntimeError("Recovery manifest does not describe exactly the expected targets")
    verified: list[str] = []
    for item in entries:
        keyword = session.get(RrugcKeywordVolumeModel, item["keyword_id"])
        if (keyword is None or keyword.tenant_id != item["tenant_id"]
                or keyword.keyword.lower() != item["keyword"].lower()
                or not keyword.picked or keyword.search_volume != item["search_volume"]):
            raise RuntimeError("Stage 0 keyword changed after reset")
        active = session.scalar(select(RrugcKeywordImageJobModel).where(
            RrugcKeywordImageJobModel.keyword_id == item["keyword_id"],
            RrugcKeywordImageJobModel.tenant_id == item["tenant_id"],
        ))
        if active is not None:
            raise RuntimeError(f"{item['keyword']}: a Stage 1 job still exists")
        saved = list(session.scalars(select(RrugcImageOutputVersionModel).where(
            RrugcImageOutputVersionModel.job_id == item["job_id"],
            RrugcImageOutputVersionModel.tenant_id == item["tenant_id"],
            RrugcImageOutputVersionModel.stage == "stage1",
        )))
        if len(saved) != 1 or saved[0].remote_file_id != item["remote_file_id"]:
            raise RuntimeError(f"{item['keyword']}: archived output pointer missing")
        processing = session.get(ProcessingJobModel, item["processing_job_id"])
        if (processing is None or processing.entity_id != item["job_id"]
                or processing.tenant_id != item["tenant_id"]):
            raise RuntimeError(f"{item['keyword']}: old processing history missing")
        verified.append(item["keyword"])
    return verified


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Perform reset after dry-run")
    parser.add_argument("--verify-audit", type=Path, default=None, help="Verify completed reset from recovery manifest")
    parser.add_argument(
        "--expected-job-ids", default="",
        help="Comma-separated three exact job IDs from dry-run; mandatory with --apply",
    )
    parser.add_argument(
        "--audit-file", type=Path, default=None,
        help="Path for a private JSON recovery manifest; mandatory with --apply",
    )
    args = parser.parse_args()
    if args.verify_audit:
        if args.apply or args.expected_job_ids or args.audit_file:
            parser.error("--verify-audit cannot be combined with apply options")
        manifest = json.loads(args.verify_audit.read_text(encoding="utf-8"))
        with SessionLocal() as session:
            keywords = verify_reset(session, manifest)
        print(json.dumps({"verification": "passed", "stage1_status": "not_run",
                          "can_generate": True, "keyword_count": len(keywords),
                          "keywords": keywords, "archived_outputs_preserved": True}, indent=2))
        return
    with SessionLocal() as session:
        candidates = inspect_candidates(session)
        print(json.dumps({
            "mode": "apply" if args.apply else "dry-run",
            "candidates": [asdict(c) for c in candidates],
        }, indent=2))
        if not args.apply:
            print("DRY RUN: no rows or files changed.")
            return
        expected_ids = {value.strip() for value in args.expected_job_ids.split(",") if value.strip()}
        if args.audit_file is None or args.audit_file.exists():
            raise RuntimeError("--audit-file must be a new, non-existing recovery manifest path")
        if len(expected_ids) != 3:
            raise RuntimeError("--expected-job-ids must contain exactly three IDs")
        # Verify the target set again while holding DB row locks, before writing.
        verified = reset_candidates(session, expected_job_ids=expected_ids)
        manifest = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "action": "reset_stage1_display_not_run",
            "deletion_scope": "rrugc_keyword_image_jobs only",
            "preserved": ["rrugc_image_output_versions", "processing_jobs", "Drive originals"],
            "legacy_version_rows_recovered": sum(c.version_count == 0 for c in verified),
            "candidates": [asdict(c) for c in verified],
        }
        args.audit_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with open(args.audit_file, "x", encoding="utf-8", opener=lambda path, flags: os.open(path, flags, 0o600)) as stream:
            json.dump(manifest, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            session.commit()
        except Exception:
            session.rollback()
            # The manifest is still retained for operator review; it is
            # evidence of the attempted transaction, not proof of success.
            raise
        print(f"RESET SUCCESS: {len(verified)} Stage 1 job links cleared; versions and Drive images preserved.")
        print(f"Recovery manifest: {args.audit_file}")


if __name__ == "__main__":
    main()
