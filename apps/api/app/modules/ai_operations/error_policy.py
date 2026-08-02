from __future__ import annotations


STORAGE_REPAIR_ERROR_CODES = frozenset({"analysis_storage_object_missing"})

_NON_RETRYABLE_ERROR_CODES = frozenset({
    "analysis_not_found",
    "analysis_image_dimensions",
    "analysis_storage_access_denied",
    "analysis_storage_object_missing",
})

_RETRYABLE_ERROR_CODES = frozenset({
    "analysis_storage_temporarily_unavailable",
})


def ai_job_retryable(error_code: str | None) -> bool:
    """Return whether an operator may directly retry this failure."""

    if not error_code or error_code in _NON_RETRYABLE_ERROR_CODES:
        return False
    return error_code in _RETRYABLE_ERROR_CODES


def ai_job_remediation(error_code: str | None) -> str | None:
    if error_code == "analysis_not_found":
        return "Analysis record missing"
    if error_code == "analysis_storage_access_denied":
        return "Check managed-storage credentials and file permissions."
    if error_code == "analysis_storage_object_missing":
        return "Repair managed storage before retrying analysis."
    if error_code == "analysis_storage_temporarily_unavailable":
        return "Managed storage is temporarily unavailable; retry later."
    if error_code == "analysis_image_dimensions":
        return "Retry only after deploying the updated image preparation code."
    return None
