"""Phase 3C.3B2C1: fail-closed Stage 1 artwork -> actual treatment provenance.

This is a READ-ONLY proof-of-lineage service, not a blinded QA verdict.
The PNG metadata's SHA-256 is only an *expected* hash; a separate
explicit, feature-gated remote-byte check proves current Drive content.

No migration, no output mutation, no QA rows, no provider generation call.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.processing.model import ProcessingJobModel
from app.modules.realistic_review_ugc.creative_brief import validate_brief
from app.modules.realistic_review_ugc.creative_v3_generation import (
    LEGACY_SCHEMA, SCHEMA,
)
from app.modules.realistic_review_ugc.model import (
    RrugcImageOutputVersionModel, RrugcKeywordCreativeBriefModel,
    RrugcKeywordImageJobModel, RrugcKeywordVolumeModel,
)

from .model import AgentHoldoutAssignmentModel, AgentHoldoutStudyModel, AgentTrialExposureModel
from .trial_challenger import BASELINE_BRIEF_VERSION, CHALLENGER_POLICY_VERSION

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_ARTWORK_BYTES = 16 * 1024 * 1024
MAX_ARTWORK_PIXELS = 64 * 1024 * 1024


class TrialArtworkLineageError(ValueError):
    """A fixed, safe reason; no keyword, prompt, raw file or provider token."""


@dataclass(frozen=True)
class ArtworkLineageClaim:
    tenant_id: str
    study_id: str
    keyword_id: str
    output_version_id: str
    image_job_id: str
    image_processing_job_id: str
    brief_processing_job_id: str
    remote_file_id: str
    sha256: str
    size_bytes: int
    width: int
    height: int
    policy_version: str
    # Arm identity is trusted internal data. Never send it to blind reviewers.
    arm: str


def _normalized_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _source_sha256(brief: RrugcKeywordCreativeBriefModel, keyword: str) -> str:
    validated = validate_brief(brief.brief_json, keyword)
    return hashlib.sha256(json.dumps(
        validated.model_dump(mode="json"), ensure_ascii=False,
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def artwork_lineage(
    session: Session, *, tenant_id: str, study_id: str, output_version_id: str,
) -> ArtworkLineageClaim:
    """Prove tenant/study/job/brief/fingerprint linkage using persisted DB rows.

    Deliberately rejects historical and regenerated source revisions if the
    original Brief identity was overwritten; current records cannot certify
    history that has been destructively replaced.
    """
    output = session.scalar(select(RrugcImageOutputVersionModel).where(
        RrugcImageOutputVersionModel.id == output_version_id,
        RrugcImageOutputVersionModel.tenant_id == tenant_id,
        RrugcImageOutputVersionModel.stage == "stage1",
    ))
    if output is None:
        raise TrialArtworkLineageError("trial_output_unavailable")
    image_job = session.scalar(select(RrugcKeywordImageJobModel).where(
        RrugcKeywordImageJobModel.id == output.job_id,
        RrugcKeywordImageJobModel.tenant_id == tenant_id,
    ))
    if (
        image_job is None or image_job.status != "completed"
        or not image_job.processing_job_id
        or not output.processing_job_id
        or output.processing_job_id != image_job.processing_job_id
        or not output.remote_file_id
    ):
        raise TrialArtworkLineageError("trial_output_job_unverified")

    job = session.scalar(select(ProcessingJobModel).where(
        ProcessingJobModel.tenant_id == tenant_id,
        ProcessingJobModel.id == output.processing_job_id,
        ProcessingJobModel.job_type == "rrugc_keyword_image_generate",
        ProcessingJobModel.entity_type == "rrugc_keyword_image_job",
        ProcessingJobModel.entity_id == image_job.id,
    ))
    if job is None or job.status != "completed":
        raise TrialArtworkLineageError("trial_image_processing_unverified")
    payload = job.payload_json
    snapshot = payload.get("creative_v3") if isinstance(payload, dict) else None
    metadata = output.font_info_json
    if (
        not isinstance(snapshot, dict)
        or not isinstance(metadata, dict)
        or metadata.get("engine") != "creative-v3"
        or metadata.get("source") != "creative-brief-v1"
        or metadata.get("technical_qa_passed") is not True
        or not isinstance(metadata.get("brief_sha256"), str)
        or not isinstance(metadata.get("plan_fingerprint"), str)
        or metadata["brief_sha256"] != snapshot.get("source_sha256")
        or metadata["plan_fingerprint"] != snapshot.get("fingerprint")
        or snapshot.get("schema_version") not in (SCHEMA, LEGACY_SCHEMA)
        or snapshot.get("provider") != "codex"
        or snapshot.get("brief_id") is None
    ):
        raise TrialArtworkLineageError("trial_image_source_unverified")

    keyword = session.scalar(select(RrugcKeywordVolumeModel).where(
        RrugcKeywordVolumeModel.tenant_id == tenant_id,
        RrugcKeywordVolumeModel.id == image_job.keyword_id,
    ))
    if (
        keyword is None or not keyword.picked
        or keyword.keyword != image_job.keyword_text
        or hashlib.sha256(keyword.keyword.encode("utf-8")).hexdigest()
           != snapshot.get("keyword_sha256")
    ):
        raise TrialArtworkLineageError("trial_image_keyword_changed")

    study = session.get(AgentHoldoutStudyModel, (tenant_id, study_id))
    assignment = session.scalar(select(AgentHoldoutAssignmentModel).where(
        AgentHoldoutAssignmentModel.tenant_id == tenant_id,
        AgentHoldoutAssignmentModel.study_id == study_id,
        AgentHoldoutAssignmentModel.keyword_id == keyword.id,
    ))
    exposure = session.scalar(select(AgentTrialExposureModel).where(
        AgentTrialExposureModel.tenant_id == tenant_id,
        AgentTrialExposureModel.study_id == study_id,
        AgentTrialExposureModel.keyword_id == keyword.id,
    ))
    if (
        study is None or study.status != "registered"
        or assignment is None or assignment.status != "exposed"
        or exposure is None or assignment.arm != exposure.arm
        or exposure.arm not in ("control", "challenger")
        or (exposure.arm == "control" and exposure.policy_version != BASELINE_BRIEF_VERSION)
        or (exposure.arm == "challenger" and exposure.policy_version != CHALLENGER_POLICY_VERSION)
        or not _SHA256.fullmatch(exposure.policy_sha256 or "")
    ):
        raise TrialArtworkLineageError("trial_actual_treatment_unverified")

    brief = session.scalar(select(RrugcKeywordCreativeBriefModel).where(
        RrugcKeywordCreativeBriefModel.tenant_id == tenant_id,
        RrugcKeywordCreativeBriefModel.keyword_id == keyword.id,
        RrugcKeywordCreativeBriefModel.id == snapshot["brief_id"],
        RrugcKeywordCreativeBriefModel.processing_job_id == exposure.processing_job_id,
    ))
    if (
        brief is None or brief.status != "completed"
        or brief.prompt_version != BASELINE_BRIEF_VERSION
        or brief.keyword_sha256 != snapshot.get("keyword_sha256")
        or not isinstance(brief.brief_json, dict)
        or _normalized_datetime(brief.completed_at) is None
        or _normalized_datetime(output.created_at) is None
        or _normalized_datetime(brief.completed_at) > _normalized_datetime(output.created_at)
        or _normalized_datetime(exposure.applied_at) > _normalized_datetime(output.created_at)
    ):
        raise TrialArtworkLineageError("trial_creative_brief_lineage_invalid")
    brief_processing = session.scalar(select(ProcessingJobModel).where(
        ProcessingJobModel.id == exposure.processing_job_id,
        ProcessingJobModel.tenant_id == tenant_id,
        ProcessingJobModel.job_type == "rrugc_creative_brief_generate",
        ProcessingJobModel.entity_type == "rrugc_keyword_creative_brief",
        ProcessingJobModel.entity_id == brief.id,
    ))
    if (
        brief_processing is None or brief_processing.status != "completed"
        or not exposure.worker_identity
    ):
        raise TrialArtworkLineageError("trial_brief_processing_unverified")

    try:
        expected_source = _source_sha256(brief, keyword.keyword)
    except (ValueError, TypeError):
        raise TrialArtworkLineageError("trial_creative_brief_invalid") from None
    if expected_source != snapshot["source_sha256"]:
        raise TrialArtworkLineageError("trial_creative_brief_source_changed")

    digest = metadata.get("content_sha256")
    if (
        not isinstance(digest, str) or not _SHA256.fullmatch(digest)
        or not isinstance(output.size_bytes, int)
        or not 0 < output.size_bytes <= MAX_ARTWORK_BYTES
        or output.content_type != "image/png"
        or not isinstance(output.width, int)
        or not isinstance(output.height, int)
        or output.width <= 0 or output.height <= 0
        or output.width * output.height > MAX_ARTWORK_PIXELS
    ):
        raise TrialArtworkLineageError("trial_artifact_metadata_unverified")

    return ArtworkLineageClaim(
        tenant_id=tenant_id, study_id=study_id, keyword_id=keyword.id,
        output_version_id=output.id, image_job_id=image_job.id,
        image_processing_job_id=job.id,
        brief_processing_job_id=exposure.processing_job_id,
        remote_file_id=output.remote_file_id, sha256=digest,
        size_bytes=output.size_bytes, width=output.width, height=output.height,
        policy_version=exposure.policy_version, arm=exposure.arm,
    )


def public_lineage_status(claim: ArtworkLineageClaim) -> dict:
    """Do not leak arm/policy, prompt text, or keyword to a blind reviewer."""
    return {
        "output_version_id": claim.output_version_id,
        "lineage_status": "database_linked",
        "brief_treatment_receipt_found": True,
        "image_processing_receipt_found": True,
        "expected_artifact_sha256": claim.sha256,
        "storage_bytes_verified": False,
        "independent_qa_verified": False,
        "eligible_for_a_b_evaluation": False,
        "evaluation_blockers": [
            "stored_artwork_bytes_not_verified",
            "independent_blinded_reviewer_unavailable",
            "trusted_provider_cost_unavailable",
        ],
    }
