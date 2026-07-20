from __future__ import annotations

from enum import Enum


class PipelineState(str, Enum):
    DISCOVERED = "discovered"
    DOWNLOAD_PENDING = "download_pending"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    DUPLICATE_DETECTED = "duplicate_detected"
    STORAGE_PENDING = "storage_pending"
    STORED = "stored"
    ANALYSIS_PENDING = "analysis_pending"
    ANALYZING = "analyzing"
    METADATA_READY = "metadata_ready"
    PROJECTION_PENDING = "projection_pending"
    PROJECTION_READY = "projection_ready"
    SEARCH_PENDING = "search_pending"
    INDEXED = "indexed"
    SIDECAR_PENDING = "sidecar_pending"
    COMPLETED = "completed"
    DOWNLOAD_FAILED = "download_failed"
    STORAGE_FAILED = "storage_failed"
    ANALYSIS_FAILED = "analysis_failed"
    PROJECTION_FAILED = "projection_failed"
    SEARCH_FAILED = "search_failed"
    SIDECAR_FAILED = "sidecar_failed"


FAILURE_STATES = {
    PipelineState.DOWNLOAD_FAILED,
    PipelineState.STORAGE_FAILED,
    PipelineState.ANALYSIS_FAILED,
    PipelineState.PROJECTION_FAILED,
    PipelineState.SEARCH_FAILED,
    PipelineState.SIDECAR_FAILED,
}

ALLOWED_TRANSITIONS: dict[PipelineState, frozenset[PipelineState]] = {
    PipelineState.DISCOVERED: frozenset({PipelineState.DOWNLOAD_PENDING}),
    PipelineState.DOWNLOAD_PENDING: frozenset({PipelineState.DOWNLOADING, PipelineState.DOWNLOAD_FAILED}),
    PipelineState.DOWNLOADING: frozenset({PipelineState.DOWNLOADED, PipelineState.DUPLICATE_DETECTED, PipelineState.DOWNLOAD_FAILED}),
    PipelineState.DOWNLOADED: frozenset({PipelineState.STORAGE_PENDING, PipelineState.ANALYSIS_PENDING, PipelineState.PROJECTION_PENDING, PipelineState.SEARCH_PENDING, PipelineState.COMPLETED}),
    PipelineState.DUPLICATE_DETECTED: frozenset({PipelineState.STORAGE_PENDING, PipelineState.ANALYSIS_PENDING, PipelineState.PROJECTION_PENDING, PipelineState.SEARCH_PENDING, PipelineState.COMPLETED}),
    PipelineState.STORAGE_PENDING: frozenset({PipelineState.STORED, PipelineState.STORAGE_FAILED}),
    PipelineState.STORED: frozenset({PipelineState.ANALYSIS_PENDING, PipelineState.PROJECTION_PENDING, PipelineState.SEARCH_PENDING, PipelineState.COMPLETED}),
    PipelineState.ANALYSIS_PENDING: frozenset({PipelineState.ANALYZING, PipelineState.METADATA_READY, PipelineState.ANALYSIS_FAILED}),
    PipelineState.ANALYZING: frozenset({PipelineState.METADATA_READY, PipelineState.ANALYSIS_FAILED}),
    PipelineState.METADATA_READY: frozenset({PipelineState.PROJECTION_PENDING, PipelineState.PROJECTION_READY, PipelineState.SEARCH_PENDING, PipelineState.COMPLETED}),
    PipelineState.PROJECTION_PENDING: frozenset({PipelineState.PROJECTION_READY, PipelineState.PROJECTION_FAILED}),
    PipelineState.PROJECTION_READY: frozenset({PipelineState.SEARCH_PENDING, PipelineState.COMPLETED}),
    PipelineState.SEARCH_PENDING: frozenset({PipelineState.INDEXED, PipelineState.SEARCH_FAILED}),
    PipelineState.INDEXED: frozenset({PipelineState.SIDECAR_PENDING, PipelineState.COMPLETED}),
    PipelineState.SIDECAR_PENDING: frozenset({PipelineState.COMPLETED, PipelineState.SIDECAR_FAILED}),
    PipelineState.DOWNLOAD_FAILED: frozenset({PipelineState.DOWNLOAD_PENDING}),
    PipelineState.STORAGE_FAILED: frozenset({PipelineState.STORAGE_PENDING}),
    PipelineState.ANALYSIS_FAILED: frozenset({PipelineState.ANALYSIS_PENDING}),
    PipelineState.PROJECTION_FAILED: frozenset({PipelineState.PROJECTION_PENDING}),
    PipelineState.SEARCH_FAILED: frozenset({PipelineState.SEARCH_PENDING}),
    PipelineState.SIDECAR_FAILED: frozenset({PipelineState.SIDECAR_PENDING, PipelineState.COMPLETED}),
    PipelineState.COMPLETED: frozenset(),
}


class InvalidPipelineTransition(ValueError):
    pass


def validate_transition(current: str, target: str) -> None:
    source = PipelineState(current)
    destination = PipelineState(target)
    if destination == source:
        return
    if destination not in ALLOWED_TRANSITIONS[source]:
        raise InvalidPipelineTransition(f"Pipeline cannot transition from {source.value} to {destination.value}")
