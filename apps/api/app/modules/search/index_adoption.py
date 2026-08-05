from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.search.governance_model import SearchIndexAuditModel, SearchIndexRecordModel
from app.modules.search.index_lifecycle import SearchIndexAdminProvider, SearchIndexLifecycleService
from app.modules.search.metrics import SEARCH_V3_METRICS
from app.modules.search.readiness import SEARCH_V3_READINESS_CACHE


@dataclass(frozen=True, slots=True)
class SearchV3AdoptionResult:
    outcome: str
    compatible: bool
    applied: bool
    active_index: str | None
    readiness: str
    missing_fields: tuple[str, ...]
    mismatched_fields: dict[str, str]
    rebuild_command: str | None

    def to_document(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "compatible": self.compatible,
            "applied": self.applied,
            "active_index": self.active_index,
            "readiness": self.readiness,
            "missing_fields": list(self.missing_fields),
            "mismatched_fields": self.mismatched_fields,
            "rebuild_command": self.rebuild_command,
        }


class SearchV3IndexAdoption:
    _FIELDS = {
        "tenant_id": ("keyword", None),
        "source_id": ("keyword", None),
        "ancestor_ids": ("keyword", None),
        "asset_id": ("keyword", None),
        "filename": ("text", "cam_text_v2"),
        "visible_text": ("text", "cam_text_v2"),
        "search_suggest": ("search_as_you_type", "cam_text_v2"),
        "search_projection_version": ("keyword", None),
    }

    def __init__(self, session: Session, provider: SearchIndexAdminProvider) -> None:
        self.session = session
        self.provider = provider

    async def run(
        self,
        *,
        index_prefix: str,
        expected_projection_version: str,
        apply: bool,
        confirmed: bool,
        actor_id: str = "search-index-cli",
    ) -> SearchV3AdoptionResult:
        if apply and not confirmed:
            raise ValueError("--apply requires --confirmed")
        aliases = await self.provider.alias_indices()
        read, write = aliases.get("read", set()), aliases.get("write", set())
        if len(read) != 1 or len(write) != 1 or read != write:
            verification = {
                "passed": False,
                "alias_matches": False,
                "failure_code": "search_v3_alias_missing",
            }
            if apply:
                self._fail_active_records(index_prefix, verification, actor_id)
                self.session.commit()
                SEARCH_V3_READINESS_CACHE.invalidate(index_prefix)
            return self._result(
                outcome="alias_missing", compatible=False, applied=apply,
                active_index=None, index_prefix=index_prefix,
                expected_projection_version=expected_projection_version,
                missing_fields=("read_alias", "write_alias"), mismatched_fields={},
            )

        index_name = next(iter(read))
        expected_name_prefix = f"{index_prefix.rstrip('-')}-v3-"
        mapping = await self.provider.index_mapping(index_name)
        settings = await self.provider.index_settings(index_name)
        count = await self.provider.index_count(index_name)
        verification = await self._verify(
            index_name=index_name,
            index_prefix=index_prefix,
            expected_name_prefix=expected_name_prefix,
            expected_projection_version=expected_projection_version,
            mapping=mapping,
            settings=settings,
            document_count=count,
        )
        compatible = bool(verification["passed"])
        missing = tuple(sorted(verification["missing_fields"]))
        mismatched = dict(sorted(verification["mismatched_fields"].items()))

        if not apply:
            return self._result(
                outcome="dry_run_compatible" if compatible else "incompatible",
                compatible=compatible, applied=False, active_index=index_name,
                index_prefix=index_prefix,
                expected_projection_version=expected_projection_version,
                missing_fields=missing, mismatched_fields=mismatched,
            )

        row = self.session.scalar(select(SearchIndexRecordModel).where(
            SearchIndexRecordModel.physical_index_name == index_name
        ))
        previous_state = row.lifecycle_state if row is not None else None
        if not compatible:
            if row is None:
                row = self._new_record(index_name, index_prefix, expected_name_prefix, expected_projection_version)
                self.session.add(row)
                self.session.flush()
            self._fail_active_records(index_prefix, verification, actor_id)
            row.lifecycle_state = "failed"
            row.document_count = count
            row.projection_version = expected_projection_version
            row.verification_json = verification
            row.verified_at = None
            self._audit(row, actor_id, "adopt_incompatible", previous_state, "failed", verification)
            self.session.commit()
            SEARCH_V3_READINESS_CACHE.invalidate(index_prefix)
            return self._result(
                outcome="incompatible", compatible=False, applied=True,
                active_index=index_name, index_prefix=index_prefix,
                expected_projection_version=expected_projection_version,
                missing_fields=missing, mismatched_fields=mismatched,
            )

        if (
            row is not None
            and row.lifecycle_state == "active"
            and row.index_prefix == index_prefix
            and row.projection_version == expected_projection_version
            and row.document_count == count
            and row.verification_json == verification
        ):
            outcome = "already_active"
        else:
            outcome = "adopted" if row is None else "repaired"
            if row is None:
                row = self._new_record(index_name, index_prefix, expected_name_prefix, expected_projection_version)
                self.session.add(row)
                self.session.flush()
            now = datetime.now(timezone.utc)
            for other in self.session.scalars(select(SearchIndexRecordModel).where(
                SearchIndexRecordModel.index_prefix == index_prefix,
                SearchIndexRecordModel.lifecycle_state == "active",
                SearchIndexRecordModel.id != row.id,
            )):
                other.lifecycle_state = "previous"
                other.retired_at = now
                self._audit(other, actor_id, "adopt_demote", "active", "previous", {})
            row.index_prefix = index_prefix
            row.index_version = index_name[len(expected_name_prefix):]
            row.projection_version = expected_projection_version
            row.lifecycle_state = "active"
            row.document_count = count
            row.verification_json = verification
            row.verified_at = now
            row.activated_at = row.activated_at or now
            row.retired_at = None
            self._audit(row, actor_id, "adopt_active_v3", previous_state, "active", verification)
            self.session.commit()
            SEARCH_V3_READINESS_CACHE.invalidate(index_prefix)
        return self._result(
            outcome=outcome, compatible=True, applied=True, active_index=index_name,
            index_prefix=index_prefix,
            expected_projection_version=expected_projection_version,
            missing_fields=(), mismatched_fields={},
        )

    async def _verify(self, **values: Any) -> dict[str, Any]:
        index_name = values["index_name"]
        mapping_body = values["mapping"].get(index_name, values["mapping"])
        mappings = mapping_body.get("mappings", {})
        properties = mappings.get("properties", {})
        missing: list[str] = []
        mismatched: dict[str, str] = {}
        field_checks: dict[str, bool] = {}
        for name, (expected_type, expected_analyzer) in self._FIELDS.items():
            actual = properties.get(name)
            if not isinstance(actual, dict):
                missing.append(name)
                field_checks[name] = False
                continue
            passed = actual.get("type") == expected_type and (
                expected_analyzer is None or actual.get("analyzer") == expected_analyzer
            )
            field_checks[name] = passed
            if not passed:
                mismatched[name] = self._expected(expected_type, expected_analyzer)
        normalized = properties.get("filename", {}).get("fields", {}).get("normalized")
        if not isinstance(normalized, dict):
            missing.append("filename.normalized")
            field_checks["filename.normalized"] = False
        else:
            normalized_ok = normalized.get("type") == "keyword" and normalized.get("normalizer") == "cam_normalized"
            field_checks["filename.normalized"] = normalized_ok
            if not normalized_ok:
                mismatched["filename.normalized"] = "type=keyword, normalizer=cam_normalized"

        definition = SearchIndexLifecycleService._definition_checks(
            values["mapping"], values["settings"], index_name, f"{values['index_prefix']}-v3"
        )
        settings_match = bool(definition["analyzer_matches"])
        if not settings_match:
            mismatched["settings.analysis"] = "cam_text_v2 analyzer and cam_punctuation filter"
        asset_id = properties.get("asset_id", {})
        cursor_sort_matches = asset_id.get("type") == "keyword" and asset_id.get("doc_values") is not False
        if not cursor_sort_matches:
            mismatched["cursor.asset_id"] = "keyword with doc_values enabled"
        if not index_name.startswith(values["expected_name_prefix"]):
            mismatched["active_index"] = f"name prefixed by {values['expected_name_prefix']}"

        projection_response = await self.provider.verification_search(index_name, {
            "size": 0,
            "track_total_hits": True,
            "_source": False,
            "query": {"bool": {"must_not": [{"term": {
                "search_projection_version": values["expected_projection_version"]
            }}]}},
        })
        projection_mismatches = SearchIndexLifecycleService._total(projection_response)
        projection_match = projection_mismatches == 0
        if not projection_match:
            mismatched["search_projection_version"] = values["expected_projection_version"]
        mapping_matches = not missing and not any(
            key not in {"settings.analysis", "cursor.asset_id", "active_index", "search_projection_version"}
            for key in mismatched
        )
        passed = mapping_matches and settings_match and cursor_sort_matches and projection_match and not mismatched
        return {
            "verification_version": 1,
            "passed": passed,
            "alias_matches": True,
            "dynamic_strict": mappings.get("dynamic") == "strict",
            "mapping_matches": mapping_matches,
            "mapping_fields": field_checks,
            "settings_match": settings_match,
            "cursor_sort_fields": {"_score": True, "asset_id": cursor_sort_matches},
            "cursor_sort_matches": cursor_sort_matches,
            "projection_version_expected": values["expected_projection_version"],
            "projection_version_documents_match": projection_match,
            "projection_version_mismatch_count": projection_mismatches,
            "document_count": values["document_count"],
            "missing_fields": sorted(missing),
            "mismatched_fields": dict(sorted(mismatched.items())),
        }

    @staticmethod
    def _expected(field_type: str, analyzer: str | None) -> str:
        return f"type={field_type}" + (f", analyzer={analyzer}" if analyzer else "")

    @staticmethod
    def _new_record(name: str, prefix: str, expected_name_prefix: str, projection: str) -> SearchIndexRecordModel:
        version = name[len(expected_name_prefix):] if name.startswith(expected_name_prefix) else "unknown"
        return SearchIndexRecordModel(
            physical_index_name=name, index_prefix=prefix,
            index_version=version or "unknown", projection_version=projection,
        )

    def _fail_active_records(self, prefix: str, verification: dict[str, Any], actor_id: str) -> None:
        for row in self.session.scalars(select(SearchIndexRecordModel).where(
            SearchIndexRecordModel.index_prefix == prefix,
            SearchIndexRecordModel.lifecycle_state == "active",
        )):
            row.lifecycle_state = "failed"
            row.verified_at = None
            row.verification_json = verification
            self._audit(row, actor_id, "adopt_fail_closed", "active", "failed", verification)

    def _audit(self, row: SearchIndexRecordModel, actor: str, action: str, old: str | None, new: str, details: dict[str, Any]) -> None:
        self.session.add(SearchIndexAuditModel(
            index_record_id=row.id, actor_id=actor, action=action,
            old_state=old, new_state=new, details_json=details,
        ))

    @staticmethod
    def _result(**values: Any) -> SearchV3AdoptionResult:
        outcome = values["outcome"]
        SEARCH_V3_METRICS.observe_adoption(outcome)
        rebuild = None
        if not values["compatible"]:
            rebuild = (
                "python -m app.operations.search_cli search:rebuild-and-reindex "
                "--tenant-id <tenant-id> "
                f"--target-projection-version {values['expected_projection_version']} "
                f"--index-prefix {values['index_prefix']} --index-generation v3 "
                "--elasticsearch-url <url>"
            )
        return SearchV3AdoptionResult(
            outcome=outcome, compatible=values["compatible"], applied=values["applied"],
            active_index=values["active_index"],
            readiness="ready" if values["compatible"] else "incompatible",
            missing_fields=values["missing_fields"], mismatched_fields=values["mismatched_fields"],
            rebuild_command=rebuild,
        )
