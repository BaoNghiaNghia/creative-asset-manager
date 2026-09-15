from enum import Enum


class CreativePlatform(str, Enum):
    ETSY = "etsy"
    AMAZON = "amazon"


class ListingTaskStatus(str, Enum):
    ACTIVE = "active"
    MISSING_SOURCE = "missing_source"
    ARCHIVED = "archived"


class PipelineRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    BLOCKED = "blocked"
    FAILED = "failed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class PipelineTriggerType(str, Enum):
    DISCOVERY = "discovery"
    MANUAL = "manual"
    REGENERATE = "regenerate"


class NodeType(str, Enum):
    INPUT_DATA = "input_data"
    IDEA_STORY = "idea_story"
    PROMPT = "prompt"
    VIDEO_GENERATION = "video_generation"
    VIDEO_OUTPUT = "video_output"
    WATERMARK_SMART_ENHANCE = "watermark_smart_enhance"


class NodeRunStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class GenerationRunStatus(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ArtifactType(str, Enum):
    INPUT_SNAPSHOT = "input_snapshot"
    INPUT_MANIFEST = "input_manifest"
    IDEA_STORY = "idea_story"
    PROMPT = "prompt"
    GENERATION_METADATA = "generation_metadata"
    RAW_VIDEO = "raw_video"
    ENHANCED_VIDEO = "enhanced_video"
    KNOWLEDGE_SNAPSHOT = "knowledge_snapshot"


ASPECT_RATIOS = frozenset({"1:1", "16:9", "9:16"})