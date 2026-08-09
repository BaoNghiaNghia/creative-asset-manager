from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4


class InventoryStorageError(RuntimeError):
    pass


def _safe_segment(value: str) -> str:
    if (
        not value
        or value in {".", ".."}
        or any(character in value for character in ("/", "\\", "\x00"))
    ):
        raise InventoryStorageError("invalid_inventory_storage_identity")
    return value


@dataclass(slots=True)
class PendingInventoryObject:
    root: Path
    temporary_path: Path
    tenant_id: str
    source_file_id: str
    sha256: str
    size_bytes: int

    def commit(self, suffix: str) -> str:
        safe_suffix = suffix.lower() if suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".avif"} else ""
        relative = Path("inventory") / self.tenant_id / "source" / self.source_file_id / f"{self.sha256}{safe_suffix}"
        destination = self.root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            self.discard()
        else:
            os.replace(self.temporary_path, destination)
        return relative.as_posix()

    def discard(self) -> None:
        self.temporary_path.unlink(missing_ok=True)


class InventorySourceStorage:
    """Atomic filesystem storage isolated under the Inventory namespace."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    async def prepare(
        self,
        *,
        tenant_id: str,
        source_file_id: str,
        body: AsyncIterator[bytes],
        max_bytes: int,
    ) -> PendingInventoryObject:
        tenant_id = _safe_segment(tenant_id)
        source_file_id = _safe_segment(source_file_id)
        temporary_directory = (
            self.root / "inventory" / tenant_id / "source" / ".tmp"
        )
        temporary_directory.mkdir(parents=True, exist_ok=True)
        temporary_path = temporary_directory / f"{source_file_id}-{uuid4().hex}.partial"
        digest = hashlib.sha256()
        total = 0
        try:
            with temporary_path.open("xb") as target:
                async for chunk in body:
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        raise InventoryStorageError("inventory_source_too_large")
                    digest.update(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            return PendingInventoryObject(
                root=self.root,
                temporary_path=temporary_path,
                tenant_id=tenant_id,
                source_file_id=source_file_id,
                sha256=digest.hexdigest(),
                size_bytes=total,
            )
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
