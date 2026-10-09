"""Immutable version history for completed skill-generated image files."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.model import RrugcImageOutputVersionModel, RrugcStage2JobModel


def save_output_version(
    session: Session, *, tenant_id: str, stage: str, job: object,
    remote_file_id: str, content_type: str | None, size_bytes: int | None,
    width: int | None, height: int | None, processing_job_id: str | None,
) -> int:
    """Called while the logical job row is locked, before changing its latest output."""
    rows = list(session.scalars(select(RrugcImageOutputVersionModel).where(
        RrugcImageOutputVersionModel.tenant_id == tenant_id,
        RrugcImageOutputVersionModel.stage == stage,
        RrugcImageOutputVersionModel.job_id == job.id,
    ).order_by(RrugcImageOutputVersionModel.version)))
    if not rows and getattr(job, "output_remote_file_id", None):
        # Pre-migration job: preserve its existing output in the immutable history.
        legacy = RrugcImageOutputVersionModel(
            id=str(uuid4()), tenant_id=tenant_id, stage=stage, job_id=job.id,
            version=1, remote_file_id=job.output_remote_file_id,
            content_type=getattr(job, "output_content_type", None),
            size_bytes=getattr(job, "output_size_bytes", None),
            width=getattr(job, "output_width", None),
            height=getattr(job, "output_height", None),
            created_at=getattr(job, "completed_at", None) or datetime.now(timezone.utc),
        )
        session.add(legacy)
        rows.append(legacy)
    if rows and processing_job_id is not None and rows[-1].processing_job_id == processing_job_id:
        return rows[-1].version
    version = rows[-1].version + 1 if rows else 1
    session.add(RrugcImageOutputVersionModel(
        id=str(uuid4()), tenant_id=tenant_id, stage=stage, job_id=job.id,
        version=version, processing_job_id=processing_job_id,
        remote_file_id=remote_file_id, content_type=content_type,
        size_bytes=size_bytes, width=width, height=height,
        created_at=datetime.now(timezone.utc),
    ))
    return version


def output_versions(session: Session, *, tenant_id: str, stage: str, job: object) -> list[dict]:
    if stage == "stage4":
        root = job.regenerated_from_job_id or job.id
        lineage = list(session.scalars(select(RrugcStage2JobModel).where(
            RrugcStage2JobModel.tenant_id == tenant_id,
            or_(RrugcStage2JobModel.id == root,
                RrugcStage2JobModel.regenerated_from_job_id == root),
            RrugcStage2JobModel.status == "completed",
            RrugcStage2JobModel.output_remote_file_id.is_not(None),
        ).order_by(RrugcStage2JobModel.created_at.asc(), RrugcStage2JobModel.id.asc())))
        return list(reversed([{
            "version": index, "remote_file_id": version_job.output_remote_file_id,
            "content_type": version_job.output_content_type,
            "size_bytes": version_job.output_size_bytes,
            "width": version_job.output_width,
            "height": version_job.output_height,
            "created_at": version_job.completed_at or version_job.created_at,
        } for index, version_job in enumerate(lineage, start=1)]))
    rows = list(session.scalars(select(RrugcImageOutputVersionModel).where(
        RrugcImageOutputVersionModel.tenant_id == tenant_id,
        RrugcImageOutputVersionModel.stage == stage,
        RrugcImageOutputVersionModel.job_id == job.id,
    ).order_by(RrugcImageOutputVersionModel.version.desc())))
    return [{
        "version": r.version, "remote_file_id": r.remote_file_id,
        "content_type": r.content_type, "size_bytes": r.size_bytes,
        "width": r.width, "height": r.height, "created_at": r.created_at,
    } for r in rows] or ([
        {"version": 1, "remote_file_id": job.output_remote_file_id,
         "content_type": job.output_content_type, "size_bytes": job.output_size_bytes,
         "width": job.output_width, "height": job.output_height,
         "created_at": job.completed_at or job.created_at}
    ] if job.output_remote_file_id else [])
