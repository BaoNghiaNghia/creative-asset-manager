from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.search.governance_model import SearchIndexAuditModel, SearchIndexRecordModel


class SearchIndexAdminProvider(Protocol):
    async def alias_indices(self) -> dict[str, set[str]]: ...
    async def index_count(self, index_name: str) -> int: ...
    async def index_mapping(self, index_name: str) -> dict[str, Any]: ...
    async def delete_index(self, index_name: str) -> None: ...
    async def switch_aliases(self, target_index: str): ...
    async def verification_search(self, index_name: str, body: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class VerificationSpec:
    expected_projection_version: str
    minimum_document_count: int = 1
    maximum_indexing_failures: int = 0
    required_queries: tuple[dict[str, Any], ...] = ()
    tenant_ids: tuple[str, ...] = ()


class IndexVerificationError(RuntimeError):
    pass


class SearchIndexLifecycleService:
    def __init__(self, session: Session, provider: SearchIndexAdminProvider):
        self.session = session
        self.provider = provider

    def register(self, *, physical_index_name: str, index_prefix: str, index_version: str, projection_version: str) -> SearchIndexRecordModel:
        row = self.session.scalar(select(SearchIndexRecordModel).where(SearchIndexRecordModel.physical_index_name == physical_index_name))
        if row:
            return row
        row = SearchIndexRecordModel(
            physical_index_name=physical_index_name, index_prefix=index_prefix,
            index_version=index_version, projection_version=projection_version,
        )
        self.session.add(row)
        self.session.flush()
        return row

    async def verify(self, record_id: str, spec: VerificationSpec, *, actor_id: str) -> SearchIndexRecordModel:
        row = self._locked(record_id)
        row.lifecycle_state = "validating"
        mapping = await self.provider.index_mapping(row.physical_index_name)
        dynamic = self._dynamic(mapping, row.physical_index_name)
        count = await self.provider.index_count(row.physical_index_name)
        checks: dict[str, Any] = {
            "dynamic_strict": dynamic == "strict",
            "document_count": count,
            "projection_version_matches": row.projection_version == spec.expected_projection_version,
            "indexing_failures": row.indexing_failure_count,
            "fixtures": [],
            "tenant_isolation": True,
        }
        for query in spec.required_queries:
            response = await self.provider.verification_search(row.physical_index_name, query)
            hits = response.get("hits", {}).get("hits", [])
            checks["fixtures"].append(bool(hits))
            expected_tenant = query.get("_expected_tenant")
            if expected_tenant:
                checks["tenant_isolation"] = checks["tenant_isolation"] and all(
                    hit.get("_source", {}).get("tenant_id") == expected_tenant for hit in hits
                )
        passed = (
            checks["dynamic_strict"]
            and count >= spec.minimum_document_count
            and row.indexing_failure_count <= spec.maximum_indexing_failures
            and checks["projection_version_matches"]
            and all(checks["fixtures"])
            and checks["tenant_isolation"]
        )
        checks["passed"] = passed
        row.document_count = count
        row.verification_json = checks
        row.verified_at = datetime.now(timezone.utc) if passed else None
        row.lifecycle_state = "building" if passed else "failed"
        self._audit(row, actor_id, "verify", "validating", row.lifecycle_state, checks)
        self.session.flush()
        if not passed:
            raise IndexVerificationError("search index verification failed")
        return row

    async def activate(self, record_id: str, *, actor_id: str) -> SearchIndexRecordModel:
        row = self._locked(record_id)
        if not row.verified_at or not (row.verification_json or {}).get("passed"):
            raise IndexVerificationError("index must pass verification before activation")
        aliases_before = await self.provider.alias_indices()
        switch = await self.provider.switch_aliases(row.physical_index_name)
        aliases_after = await self.provider.alias_indices()
        if row.physical_index_name not in aliases_after.get("read", set()) or row.physical_index_name not in aliases_after.get("write", set()):
            raise IndexVerificationError("alias switch was not observable")
        now = datetime.now(timezone.utc)
        for previous in self.session.scalars(select(SearchIndexRecordModel).where(
            SearchIndexRecordModel.index_prefix == row.index_prefix,
            SearchIndexRecordModel.lifecycle_state == "active",
            SearchIndexRecordModel.id != row.id,
        ).with_for_update()):
            previous.lifecycle_state = "previous"
            previous.retired_at = now
        old = row.lifecycle_state
        row.lifecycle_state = "active"
        row.activated_at = now
        self._audit(row, actor_id, "activate", old, "active", {
            "aliases_before": {key: sorted(value) for key, value in aliases_before.items()},
            "aliases_after": {key: sorted(value) for key, value in aliases_after.items()},
            "previous_read_indices": list(switch.previous_read_indices),
        })
        self.session.flush()
        return row

    async def cleanup(
        self, *, index_prefix: str, actor_id: str, min_age: timedelta,
        preserve_previous: int, limit: int = 20, dry_run: bool = True, confirmed: bool = False,
    ) -> list[str]:
        if not dry_run and not confirmed:
            raise ValueError("index deletion requires explicit confirmation")
        cutoff = datetime.now(timezone.utc) - min_age
        previous = list(self.session.scalars(select(SearchIndexRecordModel).where(
            SearchIndexRecordModel.index_prefix == index_prefix,
            SearchIndexRecordModel.lifecycle_state == "previous",
        ).order_by(SearchIndexRecordModel.activated_at.desc()).limit(preserve_previous)))
        protected_previous = {row.id for row in previous}
        candidates = list(self.session.scalars(select(SearchIndexRecordModel).where(
            SearchIndexRecordModel.index_prefix == index_prefix,
            SearchIndexRecordModel.lifecycle_state.in_(("previous", "retired", "deletion_pending")),
            SearchIndexRecordModel.retired_at.is_not(None),
            SearchIndexRecordModel.retired_at < cutoff,
        ).order_by(SearchIndexRecordModel.retired_at).limit(limit * 2).with_for_update()))
        output: list[str] = []
        for row in candidates:
            if row.id in protected_previous:
                continue
            aliases = await self.provider.alias_indices()
            if row.physical_index_name in aliases.get("read", set()) | aliases.get("write", set()):
                continue
            output.append(row.physical_index_name)
            if dry_run:
                if len(output) >= limit:
                    break
                continue
            old = row.lifecycle_state
            row.lifecycle_state = "deletion_pending"
            row.deletion_requested_at = datetime.now(timezone.utc)
            self.session.flush()
            aliases = await self.provider.alias_indices()  # race guard immediately before delete
            if row.physical_index_name in aliases.get("read", set()) | aliases.get("write", set()):
                row.lifecycle_state = old
                self._audit(row, actor_id, "delete_aborted_alias_protected", "deletion_pending", old, {})
                continue
            await self.provider.delete_index(row.physical_index_name)
            row.lifecycle_state = "deleted"
            row.deleted_at = datetime.now(timezone.utc)
            self._audit(row, actor_id, "delete", old, "deleted", {})
            if len(output) >= limit:
                break
        self.session.flush()
        return output

    def _locked(self, record_id: str) -> SearchIndexRecordModel:
        row = self.session.scalar(select(SearchIndexRecordModel).where(SearchIndexRecordModel.id == record_id).with_for_update())
        if row is None:
            raise LookupError(record_id)
        return row

    def _audit(self, row, actor_id, action, old_state, new_state, details):
        self.session.add(SearchIndexAuditModel(
            index_record_id=row.id, actor_id=actor_id, action=action,
            old_state=old_state, new_state=new_state, details_json=details,
        ))

    @staticmethod
    def _dynamic(mapping: dict[str, Any], index_name: str) -> str | None:
        body = mapping.get(index_name, mapping)
        return body.get("mappings", {}).get("dynamic") if isinstance(body, dict) else None
