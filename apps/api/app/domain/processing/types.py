from enum import Enum


class JobType(str, Enum):
    SOURCE_ASSET_DOWNLOAD = "source_asset_download"
    SOURCE_SYNC = "source_sync"
    ASSET_STORE = "asset_store"
    ASSET_ANALYZE = "asset_analyze"
    VIDEO_ANALYZE = "video_analyze"
    VIDEO_SEARCH_INDEX = "video_search_index"
    AI_BATCH_PREPARE = "ai_batch_prepare"
    AI_BATCH_SUBMIT = "ai_batch_submit"
    AI_BATCH_POLL = "ai_batch_poll"
    AI_BATCH_IMPORT = "ai_batch_import"
    AI_BATCH_RETRY_ITEMS = "ai_batch_retry_items"
    SEARCH_PROJECTION_BUILD = "search_projection_build"
    ASSET_INDEX = "asset_index"
    SEARCH_INDEX_SYNC = "search_index_sync"
    METADATA_SIDECAR_EXPORT = "metadata_sidecar_export"
    RETENTION_CLEANUP = "retention_cleanup"
    MANAGED_STORAGE_CLEANUP = "managed_storage_cleanup"
    IMAGE_GENERATE = "image_generate"


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
