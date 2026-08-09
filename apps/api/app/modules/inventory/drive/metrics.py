from __future__ import annotations

from collections import Counter
from threading import Lock


class InventoryDriveMetrics:
    """Bounded, label-safe process counters for Inventory ingestion."""

    _ALLOWED = frozenset(
        {
            "poll_started", "poll_completed", "files_listed",
            "provider_version_created", "provider_version_duplicate",
            "unsupported", "folder_ignored", "download_job_created",
            "download_succeeded", "download_retryable_failure",
            "download_terminal_failure", "download_bytes", "duplicate_content",
        }
    )

    def __init__(self) -> None:
        self._counts: Counter[str] = Counter()
        self._lock = Lock()

    def increment(self, name: str, value: int = 1) -> None:
        if name not in self._ALLOWED:
            raise ValueError(f"Unknown Inventory metric: {name}")
        with self._lock:
            self._counts[name] += value

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)


inventory_drive_metrics = InventoryDriveMetrics()
