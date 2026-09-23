from __future__ import annotations

from dataclasses import dataclass


GIB = 1024**3

ROLLOUT_MODE_ENCODER_ONLY = "encoder_only"
ROLLOUT_MODE_PROGRESSIVE_INDEXING = "progressive_indexing"
ROLLOUT_MODE_FULL_MIGRATION = "full_migration"
ROLLOUT_MODES = (
    ROLLOUT_MODE_ENCODER_ONLY,
    ROLLOUT_MODE_PROGRESSIVE_INDEXING,
    ROLLOUT_MODE_FULL_MIGRATION,
)


@dataclass(frozen=True, slots=True)
class DiskRolloutPolicy:
    mode: str
    minimum_before_bytes: int
    minimum_after_bytes: int
    permits_full_migration: bool


_ENCODER_ONLY_POLICY = DiskRolloutPolicy(
    mode=ROLLOUT_MODE_ENCODER_ONLY,
    minimum_before_bytes=6 * GIB,
    minimum_after_bytes=4 * GIB,
    permits_full_migration=False,
)

_PROGRESSIVE_INDEXING_POLICY = DiskRolloutPolicy(
    mode=ROLLOUT_MODE_PROGRESSIVE_INDEXING,
    minimum_before_bytes=8 * GIB,
    minimum_after_bytes=6 * GIB,
    permits_full_migration=False,
)

_FULL_MIGRATION_POLICY = DiskRolloutPolicy(
    mode=ROLLOUT_MODE_FULL_MIGRATION,
    minimum_before_bytes=12 * GIB,
    minimum_after_bytes=12 * GIB,
    permits_full_migration=True,
)


def normalize_rollout_mode(value: str | None) -> str:
    mode = (value or ROLLOUT_MODE_FULL_MIGRATION).strip().casefold()
    if mode not in ROLLOUT_MODES:
        raise ValueError(
            "rollout_mode must be one of: " + ", ".join(ROLLOUT_MODES)
        )
    return mode


def disk_rollout_policy(value: str | None) -> DiskRolloutPolicy:
    mode = normalize_rollout_mode(value)
    if mode == ROLLOUT_MODE_ENCODER_ONLY:
        return _ENCODER_ONLY_POLICY
    if mode == ROLLOUT_MODE_PROGRESSIVE_INDEXING:
        return _PROGRESSIVE_INDEXING_POLICY
    return _FULL_MIGRATION_POLICY