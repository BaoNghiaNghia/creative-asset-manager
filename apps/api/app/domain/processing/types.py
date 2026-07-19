from enum import Enum


class JobType(str, Enum):
    SOURCE_ASSET_DOWNLOAD = "source_asset_download"
    SOURCE_SYNC = "source_sync"
    ASSET_STORE = "asset_store"
    ASSET_ANALYZE = "asset_analyze"
    SEARCH_PROJECTION_BUILD = "search_projection_build"
    ASSET_INDEX = "asset_index"
    METADATA_SIDECAR_EXPORT = "metadata_sidecar_export"


JOB_TYPES = tuple(job_type.value for job_type in JobType)


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    RETRY = "retry"
    COMPLETED = "completed"
    FAILED = "failed"


class OutboxStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    PUBLISHED = "published"
    FAILED = "failed"
