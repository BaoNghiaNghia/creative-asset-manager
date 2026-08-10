from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from app.modules.inventory.drive.storage import InventoryStorageError, _safe_segment


def _safe_hash(value: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value.lower()):
        raise InventoryStorageError("invalid_inventory_prepared_hash")
    return value.lower()


class InventoryPreparedStorage:
    """Atomic prepared-artifact storage under the Inventory namespace only."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def source_path(self, *, tenant_id: str, storage_key: str) -> Path:
        tenant = _safe_segment(tenant_id)
        candidate = (self.root / storage_key).resolve()
        permitted = (self.root / "inventory" / tenant / "source").resolve()
        if permitted != candidate and permitted not in candidate.parents:
            raise InventoryStorageError("inventory_prepare_source_storage_invalid")
        if not candidate.is_file():
            raise InventoryStorageError("inventory_prepare_source_storage_missing")
        return candidate

    def write_atomic(
        self, *, tenant_id: str, source_file_id: str, preparation_version: int, content_hash: str, content: bytes
    ) -> str:
        tenant = _safe_segment(tenant_id)
        source_file = _safe_segment(source_file_id)
        digest = _safe_hash(content_hash)
        if preparation_version <= 0:
            raise InventoryStorageError("invalid_inventory_preparation_version")
        directory = self.root / "inventory" / tenant / "prepared" / source_file / f"v{preparation_version}"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{digest}.jpg"
        if target.exists():
            return target.relative_to(self.root).as_posix()
        temporary = directory / f".{digest}-{uuid4().hex}.partial"
        try:
            with temporary.open("xb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            try:
                os.replace(temporary, target)
            except FileExistsError:
                temporary.unlink(missing_ok=True)
            return target.relative_to(self.root).as_posix()
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
