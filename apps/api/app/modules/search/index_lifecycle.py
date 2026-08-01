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
    async def index_settings(self, index_name: str) -> dict[str, Any]: ...
    async def delete_index(self, index_name: str) -> None: ...
    async def switch_aliases(self, target_index: str): ...
    async def verification_search(self, index_name: str, body: dict[str, Any]) -> dict[str, Any]: ...

@dataclass(frozen=True, slots=True)
class VerificationSpec:
    expected_projection_version: str
    minimum_document_count: int = 1
    maximum_indexing_failures: int = 0
    expected_document_count: int | None = None
    document_count_tolerance: int = 0
    required_queries: tuple[dict[str, Any], ...] = ()
    tenant_ids: tuple[str, ...] = ()

class IndexVerificationError(RuntimeError):
    pass

_ALLOWED = {
    "building": {"validating", "failed"},
    "validating": {"verified", "failed"},
    "failed": {"validating"},
    "verified": {"validating", "activating"},
    "activating": {"active", "verified", "failed"},
    "active": {"previous"},
    "previous": {"activating", "retired"},
    "retired": {"deletion_pending"},
    "deletion_pending": {"retired", "deleted"},
    "deleted": set(),
}
_REQUIRED_MAPPING = {
    "asset_id": ("keyword", None), "tenant_id": ("keyword", None),
    "filename": ("text", "cam_text_v2"), "folder_path": ("text", "cam_text_v2"),
    "search_text": ("text", "cam_text_v2"), "search_terms": ("keyword", None),
    "normalized_terms": ("keyword", None), "phrases": ("keyword", None),
    "numbers": ("keyword", None), "facets": ("flattened", None),
    "path_values": ("nested", None), "metadata_profile": ("keyword", None),
    "metadata_profile_version": ("keyword", None),
    "search_projection_version": ("keyword", None),
}
_V3_REQUIRED_MAPPING = {
    "source_id": ("keyword", None),
    "parent_id": ("keyword", None),
    "ancestor_ids": ("keyword", None),
    "visible_text": ("text", "cam_text_v2"),
    "search_suggest": ("search_as_you_type", "cam_text_v2"),
}

class SearchIndexLifecycleService:
    def __init__(self, session: Session, provider: SearchIndexAdminProvider):
        self.session = session
        self.provider = provider

    def register(self, *, physical_index_name, index_prefix, index_version, projection_version):
        row = self.session.scalar(select(SearchIndexRecordModel).where(
            SearchIndexRecordModel.physical_index_name == physical_index_name
        ))
        if row:
            return row
        row = SearchIndexRecordModel(
            physical_index_name=physical_index_name, index_prefix=index_prefix,
            index_version=index_version, projection_version=projection_version,
        )
        self.session.add(row)
        self.session.flush()
        return row

    async def verify(self, record_id, spec: VerificationSpec, *, actor_id):
        row = self._locked(record_id)
        if row.lifecycle_state not in {"building", "failed", "verified", "validating"}:
            raise IndexVerificationError(f"cannot verify index in {row.lifecycle_state} state")
        old = row.lifecycle_state
        if old != "validating":
            self._transition(row, "validating")
            self._audit(row, actor_id, "verify_started", old, "validating", {})
            self.session.commit()
            row = self._locked(record_id)
        try:
            mapping = await self.provider.index_mapping(row.physical_index_name)
            settings = await self.provider.index_settings(row.physical_index_name)
            count = await self.provider.index_count(row.physical_index_name)
            checks = self._definition_checks(mapping, settings, row.physical_index_name, row.index_prefix)
            checks.update({
                "document_count": count,
                "document_count_within_tolerance": self._count_valid(count, spec),
                "projection_version_matches_record": row.projection_version == spec.expected_projection_version,
                "projection_version_documents_match": await self._projection_documents_match(row.physical_index_name, spec.expected_projection_version),
                "indexing_failures": row.indexing_failure_count,
                "indexing_failures_within_threshold": row.indexing_failure_count <= spec.maximum_indexing_failures,
                "fixtures": [],
                "tenant_isolation": [],
            })
            for fixture in spec.required_queries:
                search_body = {key: value for key, value in fixture.items() if not str(key).startswith("_")}
                response = await self.provider.verification_search(row.physical_index_name, search_body)
                checks["fixtures"].append(self._fixture_result(fixture, response))
            for tenant_id in spec.tenant_ids:
                response = await self.provider.verification_search(row.physical_index_name, {
                    "size": 100,
                    "query": {"bool": {"filter": [{"term": {"tenant_id": tenant_id}}]}},
                })
                hits = response.get("hits", {}).get("hits", [])
                checks["tenant_isolation"].append({
                    "tenant_id": tenant_id,
                    "passed": bool(hits) and all(
                        hit.get("_source", {}).get("tenant_id") == tenant_id for hit in hits
                    ),
                    "hit_count": len(hits),
                })
            passed = (
                checks["dynamic_strict"] and checks["mapping_matches"]
                and checks["analyzer_matches"] and count >= spec.minimum_document_count
                and checks["document_count_within_tolerance"]
                and checks["projection_version_matches_record"]
                and checks["projection_version_documents_match"]
                and checks["indexing_failures_within_threshold"]
                and all(item["passed"] for item in checks["fixtures"])
                and all(item["passed"] for item in checks["tenant_isolation"])
            )
            checks["passed"] = passed
            row.document_count = count
            row.verification_json = checks
            row.verified_at = datetime.now(timezone.utc) if passed else None
            self._transition(row, "verified" if passed else "failed")
            self._audit(row, actor_id, "verify", "validating", row.lifecycle_state, checks)
            self.session.commit()
            if not passed:
                raise IndexVerificationError("search index verification failed")
            return row
        except IndexVerificationError:
            raise
        except Exception as exc:
            row = self._locked(record_id)
            if row.lifecycle_state == "validating":
                self._transition(row, "failed")
            row.verified_at = None
            row.verification_json = {"passed": False, "error_category": "provider_error"}
            self._audit(row, actor_id, "verify_failed", "validating", "failed", row.verification_json)
            self.session.commit()
            raise IndexVerificationError("search index verification failed") from exc

    async def activate(self, record_id, *, actor_id):
        row = self._locked(record_id)
        if row.lifecycle_state == "active":
            aliases = await self.provider.alias_indices()
            if self._aliases_target(aliases, row.physical_index_name):
                return row
            raise IndexVerificationError("database active state disagrees with aliases")
        if row.lifecycle_state not in {"verified", "activating"} or not row.verified_at or not (row.verification_json or {}).get("passed"):
            raise IndexVerificationError("index must be verified before activation")
        if row.lifecycle_state == "verified":
            self._transition(row, "activating")
            self._audit(row, actor_id, "activate_started", "verified", "activating", {})
            self.session.commit()
            row = self._locked(record_id)
        aliases_before = await self.provider.alias_indices()
        if not self._aliases_target(aliases_before, row.physical_index_name):
            await self.provider.switch_aliases(row.physical_index_name)
        return await self.reconcile_aliases(row.index_prefix, actor_id=actor_id, action="activate")

    async def rollback(self, previous_record_id, *, actor_id):
        row = self._locked(previous_record_id)
        if row.lifecycle_state not in {"previous", "activating"}:
            raise IndexVerificationError("rollback target must be the previous index")
        if row.lifecycle_state == "previous":
            self._transition(row, "activating")
            self._audit(row, actor_id, "rollback_started", "previous", "activating", {})
            self.session.commit()
            row = self._locked(previous_record_id)
        await self.provider.switch_aliases(row.physical_index_name)
        return await self.reconcile_aliases(row.index_prefix, actor_id=actor_id, action="rollback")

    async def reconcile_aliases(self, index_prefix, *, actor_id, action="reconcile"):
        aliases = await self.provider.alias_indices()
        read, write = aliases.get("read", set()), aliases.get("write", set())
        if len(read) != 1 or len(write) != 1 or read != write:
            raise IndexVerificationError("read/write aliases are missing or divergent")
        target_name = next(iter(read))
        rows = list(self.session.scalars(select(SearchIndexRecordModel).where(
            SearchIndexRecordModel.index_prefix == index_prefix
        ).with_for_update()))
        target = next((row for row in rows if row.physical_index_name == target_name), None)
        if target is None or target.lifecycle_state in {"deleted", "deletion_pending", "retired"}:
            raise IndexVerificationError("alias target has no activatable database record")
        now = datetime.now(timezone.utc)
        old_target_state = target.lifecycle_state
        for row in rows:
            if row.id == target.id:
                continue
            if row.lifecycle_state == "active":
                self._transition(row, "previous")
                row.retired_at = now
                self._audit(row, actor_id, f"{action}_demote", "active", "previous", {"alias_target": target_name})
            elif row.lifecycle_state == "previous":
                self._transition(row, "retired")
                row.retired_at = row.retired_at or now
                self._audit(row, actor_id, f"{action}_retire", "previous", "retired", {"alias_target": target_name})
        if target.lifecycle_state != "active":
            if target.lifecycle_state not in {"activating", "verified", "previous"}:
                raise IndexVerificationError(f"cannot reconcile target in {target.lifecycle_state} state")
            if target.lifecycle_state != "activating":
                self._transition(target, "activating")
            self._transition(target, "active")
        target.activated_at = now
        target.retired_at = None
        self._audit(target, actor_id, action, old_target_state, "active", {
            "aliases": {key: sorted(value) for key, value in aliases.items()}
        })
        self.session.commit()
        return target

    async def cleanup(self, *, index_prefix, actor_id, min_age, preserve_previous,
                      limit=20, dry_run=True, confirmed=False, cancellation_requested=None):
        if min_age <= timedelta(0):
            raise ValueError("minimum retention age must be positive")
        if preserve_previous < 1:
            raise ValueError("at least one previous index must remain protected")
        if not 1 <= limit <= 100:
            raise ValueError("cleanup limit must be between 1 and 100")
        if not dry_run and not confirmed:
            raise ValueError("index deletion requires explicit confirmation")
        cutoff = datetime.now(timezone.utc) - min_age
        candidates = list(self.session.scalars(select(SearchIndexRecordModel).where(
            SearchIndexRecordModel.index_prefix == index_prefix,
            SearchIndexRecordModel.lifecycle_state.in_(("retired", "deletion_pending")),
        ).order_by(SearchIndexRecordModel.retired_at, SearchIndexRecordModel.id).limit(limit).with_for_update()))
        output = []
        for row in candidates:
            if cancellation_requested and cancellation_requested():
                break
            age_marker = row.retired_at or row.created_at
            if age_marker.tzinfo is None:
                age_marker = age_marker.replace(tzinfo=timezone.utc)
            if age_marker >= cutoff:
                continue
            if await self._protected(row):
                continue
            output.append(row.physical_index_name)
            if dry_run:
                continue
            old = row.lifecycle_state
            if old != "deletion_pending":
                self._transition(row, "deletion_pending")
                row.deletion_requested_at = datetime.now(timezone.utc)
                self._audit(row, actor_id, "delete_checkpoint", old, "deletion_pending", {})
                self.session.commit()
                row = self._locked(row.id)
            if await self._protected(row):
                self._transition(row, "retired")
                self._audit(row, actor_id, "delete_aborted_alias_protected", "deletion_pending", "retired", {})
                self.session.commit()
                output.pop()
                continue
            await self.provider.delete_index(row.physical_index_name)
            self._transition(row, "deleted")
            row.deleted_at = datetime.now(timezone.utc)
            self._audit(row, actor_id, "delete", "deletion_pending", "deleted", {})
            self.session.commit()
        return output

    async def _protected(self, row):
        self.session.refresh(row)
        if row.lifecycle_state in {"active", "previous"}:
            return True
        aliases = await self.provider.alias_indices()
        return row.physical_index_name in aliases.get("read", set()) | aliases.get("write", set())

    async def _projection_documents_match(self, index_name, version):
        response = await self.provider.verification_search(index_name, {
            "size": 0,
            "query": {"bool": {"must_not": [{"term": {"search_projection_version": version}}]}},
        })
        return self._total(response) == 0

    @staticmethod
    def _fixture_result(fixture, response):
        hits = response.get("hits", {}).get("hits", [])
        actual = [str(hit.get("_source", {}).get("asset_id") or hit.get("_id")) for hit in hits]
        expected = [str(value) for value in fixture.get("_expected_asset_ids", ())]
        ranked = bool(fixture.get("_expected_ranking", True))
        passed = actual[:len(expected)] == expected if ranked else set(expected) <= set(actual)
        return {"name": str(fixture.get("_name", "fixture"))[:100], "passed": bool(expected) and passed, "expected_asset_ids": expected, "actual_asset_ids": actual[:max(10, len(expected))]}

    @staticmethod
    def _definition_checks(mapping, settings, index_name, index_prefix=""):
        mapping_body = mapping.get(index_name, mapping)
        mappings = mapping_body.get("mappings", {}) if isinstance(mapping_body, dict) else {}
        properties = mappings.get("properties", {})
        fields = {}
        mapping_matches = True
        is_v3 = str(index_prefix).rstrip("-").endswith("v3") or "-v3-" in str(index_name)
        required_mapping = dict(_REQUIRED_MAPPING)
        if is_v3:
            required_mapping.update(_V3_REQUIRED_MAPPING)
        for name, (expected_type, expected_analyzer) in required_mapping.items():
            actual = properties.get(name, {})
            passed = actual.get("type") == expected_type and (expected_analyzer is None or actual.get("analyzer") == expected_analyzer)
            fields[name] = passed
            mapping_matches = mapping_matches and passed
        path_properties = properties.get("path_values", {}).get("properties", {})
        path_value_fields_match = all(
            path_properties.get(name, {}).get("type") == "keyword"
            for name in ("path", "value")
        )
        fields["path_values.path"] = path_value_fields_match
        fields["path_values.value"] = path_value_fields_match
        mapping_matches = mapping_matches and path_value_fields_match
        filename_normalized = properties.get("filename", {}).get("fields", {}).get("normalized", {})
        normalized_matches = (
            not is_v3
            or (
                filename_normalized.get("type") == "keyword"
                and filename_normalized.get("normalizer") == "cam_normalized"
            )
        )
        fields["filename.normalized"] = normalized_matches
        mapping_matches = mapping_matches and normalized_matches
        settings_body = settings.get(index_name, settings)
        analysis = settings_body.get("settings", {}).get("index", {}).get("analysis", {}) if isinstance(settings_body, dict) else {}
        analyzer = analysis.get("analyzer", {}).get("cam_text_v2", {})
        punctuation = analysis.get("char_filter", {}).get("cam_punctuation", {})
        analyzer_matches = (
            analyzer.get("type") == "custom"
            and analyzer.get("tokenizer") == "standard"
            and analyzer.get("char_filter") == ["cam_punctuation"]
            and analyzer.get("filter") == ["lowercase", "asciifolding"]
            and punctuation.get("type") == "pattern_replace"
            and punctuation.get("pattern") == r"[\p{P}\p{S}]+"
            and punctuation.get("replacement") == " "
        )
        return {"dynamic_strict": mappings.get("dynamic") == "strict", "mapping_matches": mapping_matches, "mapping_fields": fields, "analyzer_matches": analyzer_matches}

    @staticmethod
    def _count_valid(count, spec):
        if spec.expected_document_count is None:
            return True
        return abs(count - spec.expected_document_count) <= max(0, spec.document_count_tolerance)

    @staticmethod
    def _aliases_target(aliases, target):
        return aliases.get("read", set()) == {target} and aliases.get("write", set()) == {target}

    @staticmethod
    def _total(response):
        value = response.get("hits", {}).get("total", 0)
        if isinstance(value, dict):
            value = value.get("value", 0)
        return int(value or 0)

    def _locked(self, record_id):
        row = self.session.scalar(select(SearchIndexRecordModel).where(
            SearchIndexRecordModel.id == record_id
        ).with_for_update())
        if row is None:
            raise LookupError(record_id)
        return row

    @staticmethod
    def _transition(row, new_state):
        if new_state == row.lifecycle_state:
            return
        if new_state not in _ALLOWED.get(row.lifecycle_state, set()):
            raise IndexVerificationError(f"invalid lifecycle transition {row.lifecycle_state} -> {new_state}")
        row.lifecycle_state = new_state

    def _audit(self, row, actor_id, action, old_state, new_state, details):
        self.session.add(SearchIndexAuditModel(
            index_record_id=row.id, actor_id=actor_id, action=action,
            old_state=old_state, new_state=new_state, details_json=details,
        ))
