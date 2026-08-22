from __future__ import annotations

from app.domain.processing.types import JOB_TYPES

WORKER_ROLES = ("all", "image", "video")
VIDEO_WORKER_JOB_TYPES = ("video_analyze", "video_search_index")
VIDEO_AI_JOB_TYPES = ("video_analyze",)
IMAGE_AI_JOB_TYPES = (
    "asset_analyze", "ai_batch_prepare", "ai_batch_submit", "ai_batch_poll",
    "ai_batch_import", "ai_batch_retry_items",
)
IMAGE_WORKER_JOB_TYPES = tuple(
    job_type for job_type in JOB_TYPES if job_type not in VIDEO_WORKER_JOB_TYPES
)


def allowed_job_types_for_role(role: str) -> tuple[str, ...]:
    """Return the canonical processing-job allowlist for a worker role."""
    normalized = role.strip().casefold()
    if normalized == "all":
        return JOB_TYPES
    if normalized == "image":
        return IMAGE_WORKER_JOB_TYPES
    if normalized == "video":
        return VIDEO_WORKER_JOB_TYPES
    raise ValueError("WORKER_ROLE must be one of: all, image, video")


def enabled_job_types_for_role(
    role: str, enabled_job_types: tuple[str, ...],
) -> tuple[str, ...]:
    allowed = set(allowed_job_types_for_role(role))
    return tuple(job_type for job_type in enabled_job_types if job_type in allowed)


def borrowable_job_types_for_role(
    role: str, enabled_job_types: tuple[str, ...],
) -> tuple[str, ...]:
    """Return AI work a dedicated worker may borrow when its peer is paused."""
    normalized = role.strip().casefold()
    if normalized == "image":
        borrowable = VIDEO_AI_JOB_TYPES
    elif normalized == "video":
        borrowable = IMAGE_AI_JOB_TYPES
    else:
        return ()
    return tuple(job_type for job_type in enabled_job_types if job_type in borrowable)


def runs_operational_schedulers(role: str) -> bool:
    """Only the non-video worker schedules source and maintenance work."""
    return role.strip().casefold() in {"all", "image"}
