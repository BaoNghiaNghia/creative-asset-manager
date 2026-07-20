from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean
from typing import Any, Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.ai_governance.metrics import AI_METRICS
from app.modules.ai_governance.model import AiPilotItemModel, AiPilotRunModel, AiUsageRecordModel
from app.modules.ai_governance.repository import AiGovernanceRepository
from app.modules.ai_metadata.model import AssetAiAnalysisModel, MetadataProfileModel
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.assets.model import AssetModel, AssetSourceLinkModel, ExternalSourceModel, SourceAssetModel
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository


class PilotConfirmationRequired(RuntimeError):
    def __init__(self, estimated_cost_micros: int, threshold_micros: int):
        super().__init__("Pilot estimated cost exceeds the confirmation threshold.")
        self.estimated_cost_micros = estimated_cost_micros
        self.threshold_micros = threshold_micros


@dataclass(frozen=True, slots=True)
class PilotSelection:
    asset_ids: tuple[str, ...] = ()
    external_source_id: str | None = None
    folder_path: str | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None
    maximum_items: int = 100
    sample_seed: str = "0"
    golden_queries: tuple[str, ...] = ()


class PilotService:
    def __init__(self, session: Session, settings: Settings):
        self.session = session
        self.settings = settings
        self.governance = AiGovernanceRepository(session)

    def select_assets(self, tenant_id: str, selection: PilotSelection) -> list[AssetModel]:
        statement = select(AssetModel).where(AssetModel.tenant_id == tenant_id)
        if selection.asset_ids:
            statement = statement.where(AssetModel.id.in_(selection.asset_ids))
        if selection.created_from:
            statement = statement.where(AssetModel.created_at >= selection.created_from)
        if selection.created_to:
            statement = statement.where(AssetModel.created_at <= selection.created_to)
        assets = list(self.session.scalars(statement).all())
        if selection.external_source_id or selection.folder_path:
            filtered = []
            for asset in assets:
                sources = self.session.execute(
                    select(SourceAssetModel, ExternalSourceModel)
                    .join(AssetSourceLinkModel, AssetSourceLinkModel.source_asset_id == SourceAssetModel.id)
                    .join(ExternalSourceModel, ExternalSourceModel.id == SourceAssetModel.external_source_id)
                    .where(
                        AssetSourceLinkModel.tenant_id == tenant_id,
                        AssetSourceLinkModel.asset_id == asset.id,
                        SourceAssetModel.tenant_id == tenant_id,
                        ExternalSourceModel.tenant_id == tenant_id,
                    )
                ).all()
                matches = False
                for source_asset, source in sources:
                    metadata = source_asset.source_metadata or {}
                    path = str(metadata.get("path") or metadata.get("folder_path") or "")
                    if selection.external_source_id and source.id != selection.external_source_id:
                        continue
                    if selection.folder_path and not path.startswith(selection.folder_path):
                        continue
                    matches = True
                    break
                if matches:
                    filtered.append(asset)
            assets = filtered
        assets.sort(key=lambda asset: (
            hashlib.sha256(f"{selection.sample_seed}:{asset.id}".encode()).hexdigest(),
            asset.id,
        ))
        return assets[: max(0, selection.maximum_items)]

    def create(
        self, *, tenant_id: str, metadata_profile_id: str, selection: PilotSelection,
        created_by: str, force: bool = False,
    ) -> AiPilotRunModel:
        profile = self.session.get(MetadataProfileModel, metadata_profile_id)
        if profile is None or profile.tenant_id != tenant_id:
            raise LookupError(metadata_profile_id)
        assets = self.select_assets(tenant_id, selection)
        rate = self.governance.resolve_cost_rate("gemini", self.settings.GEMINI_MODEL)
        input_units = max(1, (len(profile.prompt_template) + 3) // 4)
        per_asset = self.governance.estimate_cost(
            rate, input_units, self.settings.AI_ESTIMATED_OUTPUT_UNITS, 1
        )
        estimate = per_asset * len(assets)
        if estimate > self.settings.AI_PILOT_CONFIRMATION_THRESHOLD_MICROS and not force:
            raise PilotConfirmationRequired(
                estimate, self.settings.AI_PILOT_CONFIRMATION_THRESHOLD_MICROS
            )
        selection_json = {
            "asset_ids": list(selection.asset_ids),
            "external_source_id": selection.external_source_id,
            "folder_path": selection.folder_path,
            "created_from": selection.created_from.isoformat() if selection.created_from else None,
            "created_to": selection.created_to.isoformat() if selection.created_to else None,
            "maximum_items": selection.maximum_items,
            "sample_seed": selection.sample_seed,
            "golden_queries": list(selection.golden_queries),
        }
        run = AiPilotRunModel(
            tenant_id=tenant_id, metadata_profile_id=metadata_profile_id,
            status="running", selection_json=selection_json,
            sample_seed=selection.sample_seed, maximum_items=selection.maximum_items,
            estimated_max_cost_micros=estimate,
            currency=rate.currency if rate else "USD", created_by=created_by,
        )
        self.session.add(run)
        self.session.flush()
        for asset in assets:
            self._enqueue(run, asset.id)
        self.governance.event(
            tenant_id, "pilot_created", actor_id=created_by,
            details={"pilot_run_id": run.id, "selected_items": len(assets),
                     "estimated_max_cost_micros": estimate},
        )
        AI_METRICS.increment("pilot_progress", provider="gemini", outcome="enqueued", value=len(assets))
        self.session.flush()
        return run

    def _enqueue(self, run: AiPilotRunModel, asset_id: str, item: AiPilotItemModel | None = None) -> AiPilotItemModel:
        analysis = AiMetadataRepository(self.session).create_analysis(
            tenant_id=run.tenant_id, asset_id=asset_id,
            metadata_profile_id=run.metadata_profile_id,
            prompt_version="pilot-v1", pipeline_version="single-asset-v1",
            ai_provider="gemini", ai_model=self.settings.GEMINI_MODEL, force=True,
        )
        item = item or AiPilotItemModel(
            run_id=run.id, tenant_id=run.tenant_id, asset_id=asset_id, status="pending"
        )
        if item.id is None:
            self.session.add(item)
            self.session.flush()
        job = ProcessingRepository(self.session).create_job(
            tenant_id=run.tenant_id, job_type="asset_analyze",
            entity_type="asset_ai_analysis", entity_id=analysis.id,
            idempotency_key=f"pilot:{run.id}:{item.id}:{analysis.id}",
            payload={"analysis_id": analysis.id, "pilot_run_id": run.id},
            provider_key="gemini", provider_scope="ai",
        )
        item.analysis_id = analysis.id
        item.job_id = job.id
        item.status = "enqueued"
        item.updated_at = datetime.now(timezone.utc)
        self.session.flush()
        return item

    def cancel(self, tenant_id: str, run_id: str, actor_id: str) -> AiPilotRunModel:
        run = self._run(tenant_id, run_id)
        run.cancellation_requested = True
        run.status = "cancelled"
        run.completed_at = datetime.now(timezone.utc)
        for item in self._items(run):
            job = self.session.get(ProcessingJobModel, item.job_id) if item.job_id else None
            if item.status in {"completed", "failed", "budget_blocked"}:
                continue
            if job is not None and job.status == "processing":
                continue
            if job is not None and job.status in {"pending", "retry"}:
                job.status = "failed"
                job.last_error_code = "pilot_cancelled"
                job.last_error_message = "Pilot item was cancelled before processing."
            item.status = "cancelled"
        self.governance.event(tenant_id, "pilot_cancelled", actor_id=actor_id,
                              details={"pilot_run_id": run.id})
        self.session.flush()
        return run

    def resume(self, tenant_id: str, run_id: str, actor_id: str) -> AiPilotRunModel:
        run = self._run(tenant_id, run_id)
        run.cancellation_requested = False
        run.status = "running"
        run.completed_at = None
        for item in self._items(run):
            if item.status in {"cancelled", "failed", "budget_blocked"}:
                self._enqueue(run, item.asset_id, item)
        self.governance.event(tenant_id, "pilot_resumed", actor_id=actor_id,
                              details={"pilot_run_id": run.id})
        self.session.flush()
        return run

    def report(self, tenant_id: str, run_id: str) -> dict[str, Any]:
        run = self._run(tenant_id, run_id)
        items = self._items(run)
        analyses = {
            value.id: value for value in self.session.scalars(
                select(AssetAiAnalysisModel).where(
                    AssetAiAnalysisModel.tenant_id == tenant_id,
                    AssetAiAnalysisModel.id.in_([item.analysis_id for item in items if item.analysis_id]),
                )
            ).all()
        }
        for item in items:
            analysis = analyses.get(item.analysis_id)
            if analysis and analysis.status in {"completed", "failed", "budget_blocked"}:
                item.status = analysis.status
        counts = {status: sum(item.status == status for item in items)
                  for status in ("completed", "failed", "budget_blocked", "cancelled", "enqueued", "pending")}
        terminal = counts["completed"] + counts["failed"] + counts["budget_blocked"] + counts["cancelled"]
        if items and terminal == len(items) and run.status == "running":
            run.status = "completed"
            run.completed_at = datetime.now(timezone.utc)
        analysis_ids = list(analyses)
        usages = list(self.session.scalars(
            select(AiUsageRecordModel).where(
                AiUsageRecordModel.tenant_id == tenant_id,
                AiUsageRecordModel.analysis_id.in_(analysis_ids),
            )
        ).all()) if analysis_ids else []
        latencies = sorted(value.latency_ms for value in usages)
        completed_analyses = [value for value in analyses.values() if value.status == "completed"]
        invalid = [value for value in usages if value.outcome == "invalid_metadata"]
        schema_failures = [
            value for value in analyses.values()
            if any("schema" in str(error.get("code", "")).lower()
                   for error in (value.validation_errors_json or []))
        ]
        empty = [
            value for value in completed_analyses
            if not ((value.search_projection or {}).get("normalized_terms") or
                    (value.search_projection or {}).get("search_terms"))
        ]
        term_counts = [
            len((value.search_projection or {}).get("normalized_terms") or [])
            for value in completed_analyses
        ]
        golden = self._golden_checks(run, completed_analyses)
        provider_actual = sum(value.provider_reported_cost_micros or 0 for value in usages)
        estimated = sum(value.locally_estimated_cost_micros for value in usages)
        effective_actual = sum(
            value.provider_reported_cost_micros
            if value.provider_reported_cost_micros is not None
            else value.locally_estimated_cost_micros for value in usages
        )
        result = {
            "pilot_run_id": run.id, "tenant_id": tenant_id, "status": run.status,
            "selected_item_count": len(items), **{f"{key}_count": value for key, value in counts.items()},
            "valid_json_rate": self._rate(counts["completed"], counts["completed"] + counts["failed"]),
            "metadata_validation_failure_rate": self._rate(len(invalid), len(usages)),
            "schema_validation_failure_rate": self._rate(len(schema_failures), len(analyses)),
            "retry_rate": self._rate(sum(value.retry_count > 0 for value in usages), len(usages)),
            "latency_ms": {"average": mean(latencies) if latencies else 0,
                           "p50": self._percentile(latencies, .50),
                           "p95": self._percentile(latencies, .95)},
            "cost": {"provider_reported_micros": provider_actual,
                     "locally_estimated_micros": estimated,
                     "effective_actual_micros": effective_actual,
                     "average_per_asset_micros": (effective_actual / len(items)) if items else 0,
                     "currency": run.currency},
            "empty_projection_count": len(empty),
            "search_term_count_distribution": {
                "minimum": min(term_counts) if term_counts else 0,
                "maximum": max(term_counts) if term_counts else 0,
                "average": mean(term_counts) if term_counts else 0,
            },
            "golden_query_checks": golden,
            "zero_result_golden_queries": [item["query"] for item in golden if item["result_count"] == 0],
            "versions": [
                {"provider": provider, "model": model, "metadata_profile": profile,
                 "metadata_profile_version": profile_version,
                 "prompt_version": prompt_version,
                 "search_projection_version": projection_version}
                for provider, model, profile, profile_version, prompt_version, projection_version
                in sorted({
                    (value.ai_provider or "unknown", value.ai_model or "unknown",
                     value.metadata_profile, value.metadata_profile_version,
                     value.prompt_version, value.search_projection_version)
                    for value in analyses.values()
                }, key=lambda item: tuple(str(value or "") for value in item))
            ],
        }
        AI_METRICS.increment("empty_projections", provider="gemini", outcome="report", value=len(empty))
        self.session.flush()
        return result

    @staticmethod
    def report_csv(report: Mapping[str, Any]) -> str:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["metric", "value"])
        for key, value in report.items():
            writer.writerow([key, json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else value])
        return output.getvalue()

    def _run(self, tenant_id: str, run_id: str) -> AiPilotRunModel:
        run = self.session.scalar(select(AiPilotRunModel).where(
            AiPilotRunModel.id == run_id, AiPilotRunModel.tenant_id == tenant_id
        ))
        if run is None:
            raise LookupError(run_id)
        return run

    def _items(self, run: AiPilotRunModel) -> list[AiPilotItemModel]:
        return list(self.session.scalars(select(AiPilotItemModel).where(
            AiPilotItemModel.run_id == run.id, AiPilotItemModel.tenant_id == run.tenant_id
        ).order_by(AiPilotItemModel.created_at, AiPilotItemModel.id)).all())

    @staticmethod
    def _rate(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 6) if denominator else 0.0

    @staticmethod
    def _percentile(values: list[int], percentile: float) -> float:
        if not values:
            return 0
        return values[min(len(values) - 1, max(0, int((len(values) - 1) * percentile)))]

    @staticmethod
    def _golden_checks(run: AiPilotRunModel, analyses: Iterable[AssetAiAnalysisModel]) -> list[dict[str, Any]]:
        values = []
        searchable = [
            " ".join((analysis.search_projection or {}).get("normalized_terms") or [])
            for analysis in analyses
        ]
        for query in run.selection_json.get("golden_queries", []):
            terms = query.lower().replace(",", " ").replace('"', "").split()
            count = sum(all(term in text.split() for term in terms) for text in searchable)
            values.append({"query": query, "result_count": count})
        return values
