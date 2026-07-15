import asyncio
import hashlib
import json
import logging
import os
import unicodedata
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

import httpx

from app.modules.explorer.schema import AssetNode

logger = logging.getLogger(__name__)
PROVIDER = "google-drive"
_memory: dict[tuple[str, str], dict] = {}
_index_tasks: set[asyncio.Task] = set()


def schedule_metadata_index(coroutine) -> None:
    task = asyncio.create_task(coroutine)
    _index_tasks.add(task)
    task.add_done_callback(_index_tasks.discard)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value.lower().replace("đ", "d"))
    return "".join(character for character in normalized if not unicodedata.combining(character)).strip()


def _marker_path(ancestor_ids: list[str]) -> str:
    return "|" + "|".join(ancestor_ids) + "|" if ancestor_ids else "|"


class MetadataService:
    def __init__(self, account_id: str):
        self.account_id = account_id
        self.url = (os.getenv("DIRECTUS_URL") or "").rstrip("/")
        self.token = os.getenv("DIRECTUS_TOKEN") or ""
        self.collection = os.getenv("DIRECTUS_METADATA_COLLECTION", "asset_metadata")
        self.ttl = int(os.getenv("DIRECTUS_METADATA_TTL_SECONDS", "3600"))

    @property
    def configured(self) -> bool:
        return bool(self.url and self.token)

    @property
    def source(self) -> str:
        return "directus" if self.configured else "memory"

    def stable_id(self, item_id: str) -> str:
        value = f"{PROVIDER}:{self.account_id}:{item_id}".encode()
        return hashlib.sha256(value).hexdigest()

    def make_row(
        self,
        asset: AssetNode,
        ancestor_ids: list[str],
        ancestor_names: list[str],
        *,
        children_indexed: bool = False,
    ) -> dict:
        modified_at = asset.modified_at.isoformat() if asset.modified_at else None
        return {
            "id": self.stable_id(asset.id),
            "provider": PROVIDER,
            "account_id": self.account_id,
            "item_id": asset.id,
            "parent_id": asset.parent_id,
            "name": asset.name,
            "normalized_name": _normalize(asset.name),
            "kind": asset.kind,
            "mime_type": asset.mime_type,
            "size": asset.size,
            "modified_at": modified_at,
            "thumbnail_url": asset.thumbnail_url,
            "web_url": asset.web_url,
            "ancestor_path": _marker_path(ancestor_ids),
            "ancestor_ids": ancestor_ids,
            "ancestor_names": ancestor_names,
            "children_indexed": children_indexed,
            "indexed_at": _now() if children_indexed else None,
        }

    def to_asset(self, row: dict) -> AssetNode:
        return AssetNode(
            id=row["item_id"],
            name=row.get("name") or "Untitled",
            kind=row.get("kind") or "other",
            mime_type=row.get("mime_type") or "application/octet-stream",
            parent_id=row.get("parent_id"),
            size=row.get("size"),
            modified_at=row.get("modified_at"),
            thumbnail_url=row.get("thumbnail_url"),
            web_url=row.get("web_url"),
            has_children=row.get("kind") == "folder",
            ancestor_ids=list(row.get("ancestor_ids") or []),
            ancestor_names=list(row.get("ancestor_names") or []),
        )

    def _merge(self, existing: dict | None, incoming: dict) -> dict:
        if not existing:
            return incoming
        merged = {**existing, **incoming}
        if existing.get("children_indexed") and not incoming.get("children_indexed"):
            merged["children_indexed"] = True
            merged["indexed_at"] = existing.get("indexed_at")
        return merged

    async def get(self, item_id: str) -> dict | None:
        memory_key = (self.account_id, item_id)
        if memory_key in _memory:
            return _memory[memory_key]
        if not self.configured:
            return None

        try:
            async with httpx.AsyncClient(
                base_url=self.url,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=15,
            ) as client:
                response = await client.get(f"/items/{self.collection}/{self.stable_id(item_id)}")
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                row = response.json()["data"]
                _memory[memory_key] = row
                return row
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            logger.warning("Directus metadata lookup failed: %s", type(exc).__name__)
            return None

    async def list_subtree(self, root_id: str) -> list[dict]:
        rows = {
            row["id"]: row
            for (account_id, _), row in _memory.items()
            if account_id == self.account_id
            and (row.get("item_id") == root_id or f"|{root_id}|" in (row.get("ancestor_path") or ""))
        }
        if not self.configured:
            return list(rows.values())

        filter_value = {
            "_and": [
                {"provider": {"_eq": PROVIDER}},
                {"account_id": {"_eq": self.account_id}},
                {
                    "_or": [
                        {"item_id": {"_eq": root_id}},
                        {"ancestor_path": {"_contains": f"|{root_id}|"}},
                    ]
                },
            ]
        }
        try:
            async with httpx.AsyncClient(
                base_url=self.url,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=30,
            ) as client:
                response = await client.get(
                    f"/items/{self.collection}",
                    params={"filter": json.dumps(filter_value), "limit": -1, "fields": "*"},
                )
                response.raise_for_status()
                for row in response.json()["data"]:
                    rows[row["id"]] = row
                    _memory[(self.account_id, row["item_id"])] = row
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            logger.warning("Directus subtree read failed; using memory index: %s", type(exc).__name__)
        return list(rows.values())

    async def upsert(self, rows: list[dict]) -> None:
        if not rows:
            return

        unique = {row["id"]: row for row in rows}
        for row_id, row in list(unique.items()):
            memory_key = (self.account_id, row["item_id"])
            merged = self._merge(_memory.get(memory_key), row)
            unique[row_id] = merged
            _memory[memory_key] = merged

        if not self.configured:
            return

        try:
            async with httpx.AsyncClient(
                base_url=self.url,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=30,
            ) as client:
                ids = list(unique)
                response = await client.get(
                    f"/items/{self.collection}",
                    params={
                        "filter": json.dumps({"id": {"_in": ids}}),
                        "limit": len(ids),
                        "fields": "*",
                    },
                )
                response.raise_for_status()
                existing = {row["id"]: row for row in response.json()["data"]}
                prepared = {
                    row_id: self._merge(existing.get(row_id), row)
                    for row_id, row in unique.items()
                }

                new_rows = [row for row_id, row in prepared.items() if row_id not in existing]
                for start in range(0, len(new_rows), 200):
                    created = await client.post(
                        f"/items/{self.collection}",
                        json=new_rows[start:start + 200],
                    )
                    created.raise_for_status()

                semaphore = asyncio.Semaphore(8)

                async def update(row: dict):
                    async with semaphore:
                        updated = await client.patch(
                            f"/items/{self.collection}/{row['id']}",
                            json=row,
                        )
                        updated.raise_for_status()

                await asyncio.gather(*(
                    update(row)
                    for row_id, row in prepared.items()
                    if row_id in existing
                    and any(row.get(key) != existing[row_id].get(key) for key in row)
                ))
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            logger.warning("Directus metadata upsert failed; memory index remains available: %s", type(exc).__name__)

    async def index_listing(self, parent: AssetNode, children: list[AssetNode]) -> None:
        existing_parent = await self.get(parent.id)
        ancestor_ids = list(existing_parent.get("ancestor_ids") or []) if existing_parent else []
        ancestor_names = list(existing_parent.get("ancestor_names") or []) if existing_parent else []

        parent_row = self.make_row(parent, ancestor_ids, ancestor_names, children_indexed=True)
        child_ancestor_ids = [*ancestor_ids, parent.id]
        child_ancestor_names = [*ancestor_names, parent.name]
        child_rows = [
            self.make_row(child, child_ancestor_ids, child_ancestor_names)
            for child in children
        ]
        await self.upsert([parent_row, *child_rows])

    def needs_refresh(self, row: dict) -> bool:
        if not row.get("children_indexed"):
            return True
        value = row.get("indexed_at")
        if not value:
            return True
        try:
            indexed_at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return indexed_at < datetime.now(timezone.utc) - timedelta(seconds=self.ttl)
        except ValueError:
            return True

    def search(self, rows: list[dict], query: str, root_id: str, limit: int) -> list[AssetNode]:
        normalized_query = _normalize(query)
        tokens = [token for token in normalized_query.split() if token]
        matches: list[tuple[float, dict]] = []

        for row in rows:
            if row.get("item_id") == root_id:
                continue
            name = row.get("normalized_name") or _normalize(row.get("name") or "")
            words = [word for word in name.replace("_", " ").replace("-", " ").split() if word]
            score = 1.0 if row.get("kind") == "folder" else 0.0

            for token in tokens:
                index = name.find(token)
                if index >= 0:
                    score += 500 if index == 0 else 400 - min(index, 80)
                    continue
                if len(token) < 3:
                    break
                similarity = max(
                    (SequenceMatcher(None, token, word).ratio() for word in words),
                    default=0,
                )
                if similarity < 0.68:
                    break
                score += similarity * 150
            else:
                matches.append((score, row))

        matches.sort(key=lambda match: (-match[0], str(match[1].get("name") or "").lower()))
        return [self.to_asset(row) for _, row in matches[:limit]]
