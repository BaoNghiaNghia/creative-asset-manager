from __future__ import annotations
import asyncio, logging, time
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import re
from typing import Any, Callable
from zoneinfo import ZoneInfo
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker
from app.modules.assets.model import ExternalSourceModel
from app.modules.auth_persistence.model import OAuthConnectionModel
from app.modules.inventory.daily_sheet.config import _overlaps, DailyCountSheetConfig, DailySheetAnyConfig, DailySheetConfig, GeminiSheetAgentConfig, GeminiToolSheetAgentConfig, normalize_identifier, normalize_sku, parse_daily_sheet_config
from app.modules.inventory.daily_sheet.google_client import GoogleSheetsInventoryClient, require_sheets_scope
from app.modules.inventory.daily_sheet.parser import A1_ROWS, DailyCountSheetValidationError, DailySheetValidationError, StockRecord, build_daily_count_variances, build_variances, canonical_hash, _normalized_header, classify_daily_count_row, parse_daily_count_records, parse_stock_records, value_blocks
from app.modules.inventory.jobs.model import InventoryJobModel
from app.modules.inventory.persistence_model import InventoryDailySheetReconciliationModel, InventoryDailySheetSnapshotModel, InventorySettingsModel, inventory_utcnow
from app.modules.inventory.materials import MaterialRegistry, MaterialResolution
from app.providers.google.auth import get_connection_access_token

logger = logging.getLogger("cam.inventory.daily_sheet")

class DailySheetConfigurationError(ValueError):
    code = "invalid_configuration"

@dataclass(frozen=True, slots=True)
class SheetContext:
    tenant_id: str
    external_source_id: str
    connection_id: str
    working_file_id: str
    archive_root_id: str
    template_file_id: str | None
    target_file_id: str
    config: DailySheetAnyConfig
    scopes: tuple[str, ...]

def _parse_time(value: Any) -> datetime | None:
    if not value: return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

def _quantity_hash(blocks: list[dict[str, Any]]) -> str | None:
    normalized = []
    for block in blocks:
        rows = block.get("values") or []
        if len(rows) != 1 or not rows[0]:
            return None
        normalized.append(format(Decimal(str(rows[0][0])), "f"))
    return canonical_hash(normalized)

class InventoryDailySheetService:
    def __init__(
        self, session_factory: sessionmaker[Session], *,
        client_factory: Callable = GoogleSheetsInventoryClient,
        token_resolver: Callable = get_connection_access_token,
        clock: Callable = lambda: datetime.now(timezone.utc),
        material_semantic_matcher: Callable | None = None,
        semantic_analyzer: Any | None = None,
        agent_service: Any | None = None,
        agent_v4_service: Any | None = None,
    ):
        self.session_factory = session_factory
        self.client_factory = client_factory
        self.token_resolver = token_resolver
        self.clock = clock
        self.semantic_analyzer = semantic_analyzer
        self.material_semantic_matcher = material_semantic_matcher or (semantic_analyzer.match_material if semantic_analyzer else None)
        self.agent_service = agent_service
        self.agent_v4_service = agent_v4_service

    def _parse_v2_runtime(
        self,
        tenant_id: str,
        context: SheetContext,
        value_range: dict[str, Any],
    ) -> tuple[dict[tuple[str, str], Any], list[dict[str, Any]]]:
        """Parse V2 rows with the same read-only runtime dependencies everywhere."""
        conversion_cache: dict[tuple[str, str, str], dict[str, tuple[Decimal, str]]] = {}

        def package_conversions(**row):
            key = (str(row["item_key"]), str(row["item_name"]), str(row["category"]))
            if key not in conversion_cache:
                # Registry lookup is read-only and closes before any semantic request.
                with self.session_factory() as session:
                    registry = MaterialRegistry(session)
                    resolution = registry.resolve(
                        tenant_id,
                        source_id=context.working_file_id,
                        external_key=row["item_key"],
                        raw_name=row["item_name"],
                        category=row["category"],
                        source_row=row["source_row"],
                        sheet=row["sheet"],
                    )
                    conversion_cache[key] = (
                        registry.approved_package_conversions(tenant_id, resolution.material_id)
                        if resolution.material_id else {}
                    )
            return conversion_cache[key]

        return parse_daily_count_records(
            context.config,
            value_range,
            package_conversion_resolver=package_conversions,
            quantity_semantic_analyzer=(
                lambda payload: self.semantic_analyzer.analyze_quantity(tenant_id, payload)
            ) if self.semantic_analyzer else None,
            schema_semantic_analyzer=(
                lambda payload: self.semantic_analyzer.analyze_schema(tenant_id, payload)
            ) if self.semantic_analyzer else None,
        )

    @staticmethod
    def _google_cell_equivalent(expected: Any, actual: Any) -> bool:
        """Compare a carry-forward cell without changing its business value."""
        expected_text = str(expected).strip()
        actual_text = str(actual).strip()
        numeric = re.compile(r"^[+-]?\d+(?:[.,]\d+)?$")
        if numeric.fullmatch(expected_text) and numeric.fullmatch(actual_text):
            try:
                return Decimal(expected_text.replace(",", ".")) == Decimal(actual_text.replace(",", "."))
            except Exception:
                pass
        return expected_text == actual_text

    def _lock(self, session: Session, key: str) -> None:
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": key})

    def _context(self, tenant_id: str, *, require_enabled: bool = True) -> SheetContext:
        with self.session_factory() as session:
            settings = session.scalar(select(InventorySettingsModel).where(InventorySettingsModel.tenant_id == tenant_id, InventorySettingsModel.enabled.is_(True)))
            if settings is None or (require_enabled and not settings.daily_sheet_automation_enabled):
                raise DailySheetConfigurationError("Daily Google Sheet automation is disabled.")
            if not settings.daily_working_spreadsheet_file_id or not settings.daily_sheet_config_json:
                raise DailySheetConfigurationError("Daily Google Sheet configuration is incomplete.")
            try:
                config = parse_daily_sheet_config(settings.daily_sheet_config_json)
            except Exception as exc:
                raise DailySheetConfigurationError("Daily Google Sheet mapping is invalid.") from exc
            if not isinstance(config, (GeminiSheetAgentConfig, GeminiToolSheetAgentConfig)) and not settings.daily_archive_root_folder_id:
                raise DailySheetConfigurationError("Daily Google Sheet archive configuration is incomplete.")
            if (
                not isinstance(config, (GeminiSheetAgentConfig, GeminiToolSheetAgentConfig))
                and config.reset.mode == "restore_template"
                and not settings.daily_template_spreadsheet_file_id
            ):
                raise DailySheetConfigurationError("Template spreadsheet is required.")
            source = session.scalar(select(ExternalSourceModel).where(
                ExternalSourceModel.tenant_id == tenant_id,
                ExternalSourceModel.id == settings.external_source_id,
                ExternalSourceModel.source_type == "google_drive",
            ))
            if source is None:
                raise DailySheetConfigurationError("Configured tenant Google Drive source was not found.")
            connection_id = str((source.source_metadata or {}).get("oauth_connection_id") or "")
            connection = session.scalar(select(OAuthConnectionModel).where(
                OAuthConnectionModel.tenant_id == tenant_id,
                OAuthConnectionModel.id == connection_id,
                OAuthConnectionModel.provider == "google",
                OAuthConnectionModel.status == "active",
            ))
            if connection is None:
                raise DailySheetConfigurationError("Reconnect Google account.")
            context = SheetContext(
                tenant_id, settings.external_source_id, connection_id,
                str(settings.daily_working_spreadsheet_file_id),
                str(settings.daily_archive_root_folder_id or ""),
                settings.daily_template_spreadsheet_file_id,
                self._target_file_id(settings, config),
                config, tuple(connection.scopes_json or ()),
            )
        require_sheets_scope(list(context.scopes))
        return context

    @staticmethod
    def _source_ranges(config: DailySheetAnyConfig) -> list[str]:
        if isinstance(config, GeminiToolSheetAgentConfig):
            raise DailySheetConfigurationError(
                "Gemini Tool Sheet Agent V4 is manual shadow-only."
            )
        return [config.source.a1_range] if isinstance(config, (DailyCountSheetConfig, GeminiSheetAgentConfig)) else [item.a1_range for item in config.source_ranges]

    @staticmethod
    def _target_file_id(settings: InventorySettingsModel, config: DailySheetAnyConfig) -> str:
        if isinstance(config, DailyCountSheetConfig) and config.reconciliation.mode == "target_table":
            return str(config.reconciliation.target_spreadsheet_file_id)
        return settings.daily_target_spreadsheet_file_id or str(settings.daily_working_spreadsheet_file_id)

    def _token(self, connection_id: str) -> str:
        value = self.token_resolver(connection_id)
        return asyncio.run(value) if hasattr(value, "__await__") else str(value)

    def validate_configuration(self, tenant_id: str) -> dict[str, Any]:
        errors: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        checks: list[dict[str, Any]] = []
        try:
            context = self._context(tenant_id, require_enabled=False)
            with self.client_factory(self._token(context.connection_id)) as google:
                working = google.validate_native_spreadsheet(context.working_file_id)
                checks.append({"code": "native_working_spreadsheet", "ok": True})
                if (working.get("capabilities") or {}).get("canEdit") is False:
                    errors.append({"code": "working_spreadsheet_not_editable"})
                uses_legacy_snapshot_flow = not isinstance(
                    context.config,
                    (GeminiSheetAgentConfig, GeminiToolSheetAgentConfig),
                )
                if uses_legacy_snapshot_flow:
                    archive = google.drive_file(context.archive_root_id)
                    if archive.get("mimeType") != "application/vnd.google-apps.folder":
                        errors.append({"code": "archive_root_not_folder"})
                    if (archive.get("capabilities") or {}).get("canAddChildren") is False:
                        errors.append({"code": "archive_root_not_writable"})
                metadata = google.spreadsheet_metadata(context.working_file_id)
                if isinstance(context.config, GeminiToolSheetAgentConfig):
                    if (
                        context.config.source.spreadsheet_file_id
                        and context.config.source.spreadsheet_file_id != context.working_file_id
                    ):
                        errors.append({"code": "spreadsheet_not_authorized"})
                    tabs = {
                        str(item.get("properties", {}).get("title") or "")
                        for item in metadata.get("sheets", [])
                    }
                    missing = set(context.config.source.allowed_sheets) - tabs
                    errors.extend(
                        {"code": "configured_sheet_missing", "sheet": sheet}
                        for sheet in sorted(missing)
                    )
                    checks.append({"code": "gemini_tool_sheet_agent_metadata", "ok": not missing})
                elif isinstance(context.config, GeminiSheetAgentConfig):
                    tabs = {
                        str(item.get("properties", {}).get("title") or "")
                        for item in metadata.get("sheets", [])
                    }
                    if context.config.source.sheet not in tabs:
                        errors.append({
                            "code": "configured_sheet_missing",
                            "sheet": context.config.source.sheet,
                        })
                    else:
                        google.batch_get_values(
                            context.working_file_id,
                            [context.config.source.a1_range],
                        )
                        google.batch_get_values(
                            context.working_file_id,
                            [context.config.source.a1_range],
                            value_render_option="FORMULA",
                        )
                        checks.append({"code": "gemini_sheet_agent_source", "ok": True})
                elif isinstance(context.config, DailyCountSheetConfig):
                    tabs = {str(item.get("properties", {}).get("title") or "") for item in metadata.get("sheets", [])}
                    if context.config.source.sheet not in tabs:
                        errors.append({"code": "configured_sheet_missing", "sheet": context.config.source.sheet})
                    source_values = google.batch_get_values(context.working_file_id, self._source_ranges(context.config))
                    records = {}
                    try:
                        records, parse_warnings = self._parse_v2_runtime(
                            tenant_id, context, source_values[0] if source_values else {}
                        )
                        warnings.extend(parse_warnings)
                        if any(
                            warning.get("code") == "schema_mapping_proposed"
                            and (
                                warning.get("requires_review")
                                or warning.get("reset_relevant_changed")
                            )
                            for warning in parse_warnings
                        ):
                            errors.append({"code": "reset_schema_mapping_approval_required"})
                        checks.append({"code": "daily_count_rows", "ok": True})
                    except DailyCountSheetValidationError as exc:
                        errors.extend(exc.errors)
                        warnings.extend(exc.warnings)
                    if source_values:
                        rows = list(source_values[0].get("values") or [])
                        if len(rows) >= context.config.source.header_row:
                            header = rows[context.config.source.header_row - 1]
                            positions = {_normalized_header(value): index for index, value in enumerate(header)}
                            protected = []
                            escaped_sheet = context.config.source.sheet.replace("'", "''")
                            for semantic in ("item_key", "name", "category"):
                                index = positions.get(_normalized_header(getattr(context.config.source.columns, semantic)))
                                if index is not None:
                                    column = self._a1_column(index)
                                    protected.append(f"'{escaped_sheet}'!{column}{context.config.source.header_row}:{column}1048576")
                            if any(_overlaps(reset_range, identity_range) for reset_range in context.config.reset.ranges for identity_range in protected):
                                errors.append({"code": "reset_overlaps_identity_columns"})
                    workbook_timezone = str((metadata.get("properties") or {}).get("timeZone") or "")
                    if workbook_timezone and workbook_timezone != self._inventory_timezone(tenant_id):
                        warnings.append({"code": "workbook_timezone_mismatch", "workbook_timezone": workbook_timezone, "inventory_timezone": self._inventory_timezone(tenant_id)})
                    if context.config.reconciliation.mode == "target_table":
                        google.validate_native_spreadsheet(context.target_file_id)
                        target_blocks = google.batch_get_values(context.target_file_id, [item.item_key_range for item in context.config.reconciliation.targets])
                        available_by_warehouse = {
                            normalize_identifier(target.warehouse): {str(row[0]).strip() for row in block.get("values", []) if row and str(row[0]).strip()}
                            for target, block in zip(context.config.reconciliation.targets, target_blocks, strict=True)
                        }
                        missing = [
                            record.item_key for record in records.values()
                            if record.item_key not in available_by_warehouse.get(record.warehouse, set())
                        ]
                        errors.extend({"code": "target_item_mapping_missing", "item_key": item_key} for item_key in missing)
                else:
                    source_values = google.batch_get_values(context.working_file_id, self._source_ranges(context.config))
                    parse_stock_records(context.config, source_values)
                    google.validate_native_spreadsheet(context.target_file_id)
                    google.batch_get_values(context.target_file_id, [item.sku_range for item in context.config.targets])
                if uses_legacy_snapshot_flow and context.template_file_id:
                    google.validate_native_spreadsheet(context.template_file_id)
                    google.batch_get_values(context.template_file_id, context.config.reset.ranges, value_render_option="FORMULA")
            checks.extend({"code": error["code"], "ok": False} for error in errors)
            return {"valid": not errors, "errors": errors, "warnings": warnings, "checks": checks}
        except Exception as exc:
            error = {"code": getattr(exc, "code", "invalid_configuration"), "message": str(exc)}
            return {"valid": False, "errors": [error], "warnings": warnings, "checks": checks + [{**error, "ok": False}]}

    def _inventory_timezone(self, tenant_id: str) -> str:
        with self.session_factory() as session:
            settings = session.scalar(select(InventorySettingsModel).where(InventorySettingsModel.tenant_id == tenant_id))
            return settings.timezone if settings else "Asia/Ho_Chi_Minh"

    def discover(self, tenant_id: str, spreadsheet_id: str) -> dict[str, Any]:
        if not spreadsheet_id.strip():
            raise DailySheetConfigurationError("Working spreadsheet ID is required.")
        with self.session_factory() as session:
            settings = session.scalar(select(InventorySettingsModel).where(InventorySettingsModel.tenant_id == tenant_id))
            if settings is None:
                raise DailySheetConfigurationError("Inventory settings are required.")
            source = session.scalar(select(ExternalSourceModel).where(
                ExternalSourceModel.tenant_id == tenant_id,
                ExternalSourceModel.id == settings.external_source_id,
                ExternalSourceModel.source_type == "google_drive",
            ))
            if source is None:
                raise DailySheetConfigurationError("Configured tenant Google Drive source was not found.")
            connection_id = str((source.source_metadata or {}).get("oauth_connection_id") or "")
            connection = session.scalar(select(OAuthConnectionModel).where(
                OAuthConnectionModel.tenant_id == tenant_id,
                OAuthConnectionModel.id == connection_id,
                OAuthConnectionModel.provider == "google",
                OAuthConnectionModel.status == "active",
            ))
            if connection is None:
                raise DailySheetConfigurationError("Reconnect Google account.")
            scopes = tuple(connection.scopes_json or ())
            inventory_timezone = settings.timezone
        require_sheets_scope(list(scopes))
        with self.client_factory(self._token(connection_id)) as google:
            google.validate_native_spreadsheet(spreadsheet_id)
            metadata = google.spreadsheet_metadata(spreadsheet_id)
            tabs: list[dict[str, Any]] = []
            for tab in metadata.get("sheets", []):
                properties = tab.get("properties") or {}
                title = str(properties.get("title") or "")
                escaped = title.replace("'", "''")
                value_ranges = google.batch_get_values(spreadsheet_id, [f"'{escaped}'!A1:AZ80"], value_render_option="FORMULA")
                rows = list(value_ranges[0].get("values") or []) if value_ranges else []
                header_row = self._detect_header_row(rows)
                headers = list(rows[header_row - 1]) if header_row else []
                positions = {_normalized_header(value): index for index, value in enumerate(headers)}
                key_index = positions.get(_normalized_header("STT"), 0)
                name_index = positions.get(_normalized_header("Tên Nguyên Liệu / Vật Tư"), 1)
                counts = {"ITEM": 0, "SECTION": 0, "TOTAL": 0, "EMPTY": 0}
                samples: list[dict[str, Any]] = []
                materials: list[dict[str, Any]] = []
                category_index = positions.get(_normalized_header("Phân Loại"), 2)
                if header_row:
                    with self.session_factory() as registry_session:
                        registry = MaterialRegistry(registry_session)
                        for row_number, row in enumerate(rows[header_row:], start=header_row + 1):
                            kind = classify_daily_count_row(row, key_index=key_index, name_index=name_index)
                            counts[kind] = counts.get(kind, 0) + 1
                            if kind != "ITEM":
                                continue
                            item_key = str(row[key_index]).strip() if key_index < len(row) else ""
                            raw_name = str(row[name_index]).strip() if name_index < len(row) else ""
                            category = str(row[category_index]).strip() if category_index < len(row) else None
                            resolution = registry.resolve(
                                tenant_id, source_id=spreadsheet_id, external_key=item_key,
                                raw_name=raw_name, category=category, source_row=row_number, sheet=title,
                                semantic_matcher=self.material_semantic_matcher,
                                context={
                                    "source_cells": [f"{self._a1_column(name_index)}{row_number}"],
                                    "nearby_rows": rows[max(header_row, row_number - 3):row_number + 2],
                                },
                            )
                            material = {"row": row_number, "item_key": item_key, "name": raw_name, "category": category, "resolution": resolution.to_dict()}
                            materials.append(material)
                            if len(samples) < 5:
                                samples.append({"row": row_number, "item_key": item_key, "name": raw_name})
                formulas = any(isinstance(cell, str) and cell.startswith("=") for row in rows for cell in row)
                unresolved = [item for item in materials if item["resolution"]["requires_review"]]
                tabs.append({"title": title, "sheet_id": properties.get("sheetId"), "headers": headers, "detected_header_row": header_row, "sample_item_rows": samples, "item_count": counts["ITEM"], "materials": materials, "new_material_candidates": [item for item in unresolved if item["resolution"]["status"] == "new_material"], "possible_renames": [item for item in unresolved if item["resolution"]["status"] == "possible_rename"], "anomalies": [item for item in unresolved if item["resolution"]["status"] == "ambiguous"], "unit_package_warnings": [], "row_counts": counts, "formula_presence": formulas, "candidate_columns": self._candidate_columns(headers)})
        workbook_timezone = str((metadata.get("properties") or {}).get("timeZone") or "")
        warnings = []
        if workbook_timezone and workbook_timezone != inventory_timezone:
            warnings.append({"code": "workbook_timezone_mismatch", "workbook_timezone": workbook_timezone, "inventory_timezone": inventory_timezone})
        return {"spreadsheet_id": spreadsheet_id, "title": (metadata.get("properties") or {}).get("title"), "timezone": workbook_timezone, "tabs": tabs, "warnings": warnings}

    @staticmethod
    def _detect_header_row(rows: list[list[Any]]) -> int | None:
        for index, row in enumerate(rows[:20], start=1):
            normalized = {_normalized_header(value) for value in row}
            if _normalized_header("STT") in normalized and _normalized_header("Tên Nguyên Liệu / Vật Tư") in normalized:
                return index
        return None

    @staticmethod
    def _candidate_columns(headers: list[Any]) -> dict[str, str]:
        expected = {
            "item_key": "STT", "name": "Tên Nguyên Liệu / Vật Tư", "category": "Phân Loại",
            "opening": "SL Đầu Ca / Nhận", "used": "SL Sử Dụng Pha Chế", "inbound": "Nhập Hàng",
            "waste": "SL Huỷ / Hư Hỏng", "closing": "Tồn Cuối Ca",
        }
        available = {_normalized_header(value): str(value) for value in headers}
        return {key: available[_normalized_header(value)] for key, value in expected.items() if _normalized_header(value) in available}


    def _claim_snapshot(self, tenant_id: str, business_date: date, context: SheetContext):
        with self.session_factory() as session:
            self._lock(session, f"inventory-sheet-snapshot:{tenant_id}:{business_date}")
            row = session.scalar(select(InventoryDailySheetSnapshotModel).where(
                InventoryDailySheetSnapshotModel.tenant_id == tenant_id,
                InventoryDailySheetSnapshotModel.business_date == business_date,
            ))
            if row is None:
                row = InventoryDailySheetSnapshotModel(
                    tenant_id=tenant_id, business_date=business_date,
                    external_source_id=context.external_source_id,
                    source_spreadsheet_file_id=context.working_file_id, status="pending",
                )
                session.add(row)
                session.flush()
            if row.status == "completed":
                session.expunge(row)
                return row, False
            updated_at = row.updated_at
            if updated_at is not None and updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            if row.status in ("cloning", "resetting") and updated_at and (self.clock() - updated_at).total_seconds() < 900:
                session.expunge(row)
                return row, False
            reset_was_started = bool(
                row.snapshot_file_id
                and row.verified_at
                and row.reset_started_at
                and not row.reset_completed_at
            )
            row.status = "cloning" if not row.snapshot_file_id else ("resetting" if reset_was_started else "cloned")
            row.attempt_count += 1
            row.error_code = row.error_message = None
            session.commit()
            session.refresh(row)
            session.expunge(row)
            return row, True

    def _resume_reset(self, row_id: str, tenant_id: str, business_date: date, context: SheetContext, started: float):
        try:
            with self.client_factory(self._token(context.connection_id)) as google:
                self._reset_and_verify(google, context)
            with self.session_factory() as session:
                persisted = session.get(InventoryDailySheetSnapshotModel, row_id)
                persisted.status = "completed"
                persisted.reset_completed_at = inventory_utcnow()
                persisted.error_code = persisted.error_message = None
                session.commit()
                session.refresh(persisted)
                session.expunge(persisted)
            logger.info("inventory_daily_sheet_snapshot_reset_resumed", extra={"tenant_id": tenant_id, "business_date": str(business_date), "snapshot_id": row_id, "duration_ms": int((time.monotonic() - started) * 1000)})
            return persisted
        except Exception as exc:
            with self.session_factory() as session:
                persisted = session.get(InventoryDailySheetSnapshotModel, row_id)
                if persisted:
                    persisted.status = "retryable_failure"
                    persisted.error_code = str(getattr(exc, "code", type(exc).__name__))[:100]
                    persisted.error_message = "Inventory daily reset retry failed; inspect structured logs."
                    session.commit()
            raise

    def _agent(self):
        if self.agent_service is None:
            from app.modules.inventory.daily_sheet.agent.service import build_daily_sheet_agent_service
            self.agent_service = build_daily_sheet_agent_service(
                session_factory=self.session_factory,
                context_provider=lambda tenant_id: self._context(
                    tenant_id, require_enabled=False
                ),
                client_factory=self.client_factory,
                token_resolver=self.token_resolver,
            )
        return self.agent_service

    def _agent_v4(self):
        if self.agent_v4_service is None:
            from app.modules.inventory.daily_sheet.agent_v4.service import build_daily_sheet_v4_service
            self.agent_v4_service = build_daily_sheet_v4_service(
                session_factory=self.session_factory,
                context_provider=lambda tenant_id: self._context(
                    tenant_id, require_enabled=False
                ),
                client_factory=self.client_factory,
                token_resolver=self.token_resolver,
            )
        return self.agent_v4_service

    def is_agent_v4_configured(self, tenant_id: str) -> bool:
        context = self._context(tenant_id, require_enabled=False)
        return isinstance(context.config, GeminiToolSheetAgentConfig)

    def run_agent_v4_shadow(self, tenant_id: str, business_date: date):
        context = self._context(tenant_id, require_enabled=False)
        if not isinstance(context.config, GeminiToolSheetAgentConfig):
            raise DailySheetConfigurationError("Gemini Tool Sheet Agent V4 is not configured.")
        return self._agent_v4().run_shadow(tenant_id, business_date)

    def run_agent_v4(
        self, tenant_id: str, business_date: date, *, slot_kind: str | None = None
    ):
        context = self._context(tenant_id, require_enabled=False)
        if not isinstance(context.config, GeminiToolSheetAgentConfig):
            raise DailySheetConfigurationError("Gemini Tool Sheet Agent V4 is not configured.")
        return self._agent_v4().run(
            tenant_id, business_date, slot_kind=slot_kind
        )

    def is_agent_v3_configured(self, tenant_id: str) -> bool:
        context = self._context(tenant_id, require_enabled=False)
        return isinstance(context.config, GeminiSheetAgentConfig)

    def plan_agent_run(self, tenant_id: str, business_date: date, *, dry_run: bool = True):
        context = self._context(tenant_id, require_enabled=False)
        if not isinstance(context.config, GeminiSheetAgentConfig):
            raise DailySheetConfigurationError("Gemini Sheet Agent V3 is not configured.")
        return self._agent().plan_agent_run(tenant_id, business_date, dry_run=dry_run)

    def apply_agent_plan(self, tenant_id: str, plan, *, expected_plan_hash: str, expected_source_hash: str):
        context = self._context(tenant_id)
        if not isinstance(context.config, GeminiSheetAgentConfig):
            raise DailySheetConfigurationError("Gemini Sheet Agent V3 is not configured.")
        return self._agent().apply_agent_plan(
            tenant_id,
            plan,
            expected_plan_hash=expected_plan_hash,
            expected_source_hash=expected_source_hash,
        )

    def snapshot_and_reset(self, tenant_id: str, business_date: date):
        context = self._context(tenant_id)
        if isinstance(context.config, GeminiToolSheetAgentConfig):
            raise DailySheetConfigurationError("Gemini Tool Sheet Agent V4 is manual shadow-only.")
        if isinstance(context.config, GeminiSheetAgentConfig):
            return self.plan_agent_run(
                tenant_id,
                business_date,
                dry_run=context.config.agent.apply_mode != "auto",
            )
        row, claimed = self._claim_snapshot(tenant_id, business_date, context)
        if not claimed: return row
        started = time.monotonic()
        if row.status == "resetting":
            return self._resume_reset(row.id, tenant_id, business_date, context, started)
        try:
            with self.client_factory(self._token(context.connection_id)) as google:
                source_meta = google.validate_native_spreadsheet(context.working_file_id)
                before = _parse_time(source_meta.get("modifiedTime"))
                source_values = google.batch_get_values(context.working_file_id, self._source_ranges(context.config))
                source_hash = canonical_hash(value_blocks(source_values))
                if isinstance(context.config, DailyCountSheetConfig):
                    _records, parse_warnings = self._parse_v2_runtime(
                        tenant_id, context, source_values[0] if source_values else {}
                    )
                    if any(
                        warning.get("code") == "schema_mapping_proposed"
                        and (warning.get("requires_review") or warning.get("reset_relevant_changed"))
                        for warning in parse_warnings
                    ):
                        raise DailySheetValidationError("reset_schema_mapping_approval_required")
                snapshot_id = row.snapshot_file_id
                if not snapshot_id:
                    folder = google.ensure_archive_folder(context.archive_root_id, tenant_id=tenant_id, business_date=business_date.isoformat())
                    copied = google.copy_spreadsheet(
                        context.working_file_id, folder_id=str(folder["id"]),
                        name=f"{str(source_meta.get('name') or 'Inventory').strip()} - {business_date}",
                        tenant_id=tenant_id, business_date=business_date.isoformat(),
                    )
                    snapshot_id = str(copied["id"])
                    with self.session_factory() as session:
                        persisted = session.get(InventoryDailySheetSnapshotModel, row.id)
                        persisted.archive_folder_id = str(folder["id"])
                        persisted.snapshot_file_id = snapshot_id
                        persisted.source_modified_time_before = before
                        persisted.source_data_hash = source_hash
                        persisted.status = "cloned"
                        persisted.cloned_at = inventory_utcnow()
                        session.commit()
                snapshot_values = google.batch_get_values(snapshot_id, self._source_ranges(context.config))
                snapshot_hash = canonical_hash(value_blocks(snapshot_values))
                if snapshot_hash != source_hash:
                    raise DailySheetValidationError("snapshot_verification_mismatch")
                after = _parse_time(google.drive_file(context.working_file_id).get("modifiedTime"))
                if before != after:
                    raise DailySheetValidationError("source_changed_during_snapshot")
                with self.session_factory() as session:
                    persisted = session.get(InventoryDailySheetSnapshotModel, row.id)
                    persisted.snapshot_data_hash = snapshot_hash
                    persisted.source_modified_time_after = after
                    persisted.verified_at = inventory_utcnow()
                    persisted.reset_started_at = inventory_utcnow()
                    persisted.status = "resetting"
                    session.commit()
                self._reset_and_verify(google, context)
            with self.session_factory() as session:
                persisted = session.get(InventoryDailySheetSnapshotModel, row.id)
                persisted.status = "completed"
                persisted.reset_completed_at = inventory_utcnow()
                persisted.error_code = persisted.error_message = None
                session.commit()
                session.refresh(persisted)
                session.expunge(persisted)
            logger.info("inventory_daily_sheet_snapshot_verified", extra={"tenant_id": tenant_id, "business_date": str(business_date), "snapshot_id": row.id, "duration_ms": int((time.monotonic() - started) * 1000)})
            return persisted
        except Exception as exc:
            code = str(exc) if str(exc) in {"snapshot_verification_mismatch", "source_changed_during_snapshot", "reset_verification_failed"} else getattr(exc, "code", type(exc).__name__)
            with self.session_factory() as session:
                persisted = session.get(InventoryDailySheetSnapshotModel, row.id)
                if persisted:
                    retryable = code == "source_changed_during_snapshot" or not isinstance(exc, (DailySheetConfigurationError, DailySheetValidationError))
                    persisted.status = "retryable_failure" if retryable else "terminal_failure"
                    persisted.error_code = str(code)[:100]
                    persisted.error_message = "Inventory daily snapshot failed; inspect structured logs."
                    session.commit()
            raise

    def _reset_and_verify(self, google, context: SheetContext) -> None:
        if isinstance(context.config, GeminiToolSheetAgentConfig):
            raise DailySheetConfigurationError(
                "Gemini Tool Sheet Agent V4 is manual shadow-only."
            )
        if isinstance(context.config, DailyCountSheetConfig):
            self._reset_v2(google, context)
            return
        if context.config.reset.mode == "clear_ranges":
            google.batch_clear_values(context.working_file_id, context.config.reset.ranges)
            reset = google.batch_get_values(context.working_file_id, context.config.reset.ranges)
            if any(str(cell).strip() for block in reset for row in (block.get("values") or []) for cell in row):
                raise DailySheetValidationError("reset_verification_failed")
            return
        template = google.batch_get_values(str(context.template_file_id), context.config.reset.ranges, value_render_option="FORMULA")
        updates = [{"range": target, "majorDimension": "ROWS", "values": block.get("values", [])} for target, block in zip(context.config.reset.ranges, template, strict=True)]
        google.batch_update_values(context.working_file_id, updates)
        restored = google.batch_get_values(context.working_file_id, context.config.reset.ranges, value_render_option="FORMULA")
        if canonical_hash(value_blocks(restored)) != canonical_hash(value_blocks(template)):
            raise DailySheetValidationError("reset_verification_failed")

    def _reset_v2(self, google, context: SheetContext) -> None:
        config = context.config
        if config.reset.mode == "restore_template":
            template = google.batch_get_values(str(context.template_file_id), config.reset.ranges, value_render_option="FORMULA")
            updates = [{"range": target, "majorDimension": "ROWS", "values": block.get("values", [])} for target, block in zip(config.reset.ranges, template, strict=True)]
            google.batch_update_values(context.working_file_id, updates)
            restored = google.batch_get_values(context.working_file_id, config.reset.ranges, value_render_option="FORMULA")
            if canonical_hash(value_blocks(restored)) != canonical_hash(value_blocks(template)):
                raise DailySheetValidationError("reset_verification_failed")
            return
        source = google.batch_get_values(context.working_file_id, [config.source.a1_range])
        block = source[0] if source else {}
        rows = list(block.get("values") or [])
        records, parse_warnings = self._parse_v2_runtime(context.tenant_id, context, block)
        if any(
            warning.get("code") == "schema_mapping_proposed"
            and (warning.get("requires_review") or warning.get("reset_relevant_changed"))
            for warning in parse_warnings
        ):
            raise DailySheetValidationError("reset_schema_mapping_approval_required")
        header = rows[config.source.header_row - 1]
        positions = {_normalized_header(value): index for index, value in enumerate(header)}
        semantic_indexes = {semantic: positions[_normalized_header(heading)] for semantic, heading in config.source.columns.model_dump().items()}
        sheet = config.source.sheet.replace("'", "''")
        clear_columns = config.reset.entry_columns if config.reset.mode == "clear_entry_columns" else config.reset.clear_columns
        clear_ranges = [
            f"'{sheet}'!{self._a1_column(semantic_indexes[semantic])}{record.source_row}"
            for record in records.values() for semantic in clear_columns
        ]
        if config.reset.mode == "carry_forward":
            updates = [{
                "range": f"'{sheet}'!{self._a1_column(semantic_indexes[config.reset.carry_forward_to])}{record.source_row}",
                "majorDimension": "ROWS", "values": [[record.quantity.raw]],
            } for record in records.values()]
            if updates:
                google.batch_update_values(context.working_file_id, updates)
                carried = google.batch_get_values(
                    context.working_file_id, [update["range"] for update in updates]
                )
                if len(carried) != len(updates) or any(
                    not block.get("values")
                    or not block["values"][0]
                    or not self._google_cell_equivalent(update["values"][0][0], block["values"][0][0])
                    for update, block in zip(updates, carried, strict=False)
                ):
                    raise DailySheetValidationError("reset_verification_failed")
        if clear_ranges:
            google.batch_clear_values(context.working_file_id, clear_ranges)
            cleared = google.batch_get_values(context.working_file_id, clear_ranges)
            if any(str(cell).strip() for item in cleared for row in (item.get("values") or []) for cell in row):
                raise DailySheetValidationError("reset_verification_failed")

    @staticmethod
    def _a1_column(index: int) -> str:
        result = ""
        value = index + 1
        while value:
            value, remainder = divmod(value - 1, 26)
            result = chr(65 + remainder) + result
        return result

    def reconcile(self, tenant_id: str, business_date: date, *, dry_run: bool = False):
        context = self._context(tenant_id, require_enabled=False)
        if isinstance(context.config, (GeminiSheetAgentConfig, GeminiToolSheetAgentConfig)):
            return {
                "status": "report_only",
                "business_date": business_date.isoformat(),
                "writes": 0,
            }
        if isinstance(context.config, DailyCountSheetConfig):
            return self._reconcile_v2(tenant_id, business_date, context, dry_run=dry_run)
        if not dry_run:
            with self.session_factory() as session:
                completed = session.scalar(select(InventoryDailySheetReconciliationModel).where(
                    InventoryDailySheetReconciliationModel.tenant_id == tenant_id,
                    InventoryDailySheetReconciliationModel.business_date == business_date,
                    InventoryDailySheetReconciliationModel.status == "completed",
                ))
                if completed is not None:
                    return dict(completed.summary_json)
        current, previous = self._snapshots(tenant_id, business_date)
        if current is None: raise DailySheetValidationError("current_snapshot_incomplete")
        if previous is None:
            if not dry_run: self._await_baseline(tenant_id, business_date, current.id)
            return {"status": "awaiting_baseline", "error_code": "missing_previous_snapshot", "writes": 0}
        with self.client_factory(self._token(context.connection_id)) as google:
            current_records = parse_stock_records(context.config, google.batch_get_values(str(current.snapshot_file_id), self._source_ranges(context.config)))
            previous_records = parse_stock_records(context.config, google.batch_get_values(str(previous.snapshot_file_id), self._source_ranges(context.config)))
            variances = build_variances(current_records, previous_records)
            target_records = dict(current_records)
            for key, previous_record in previous_records.items():
                target_records.setdefault(key, StockRecord(previous_record.warehouse, previous_record.sku, Decimal(0)))
            updates = self._target_updates(google, context, target_records)
            plan_hash = canonical_hash(updates)
            before = google.batch_get_values(context.target_file_id, [item["range"] for item in updates])
            desired_hash = canonical_hash([format(Decimal(str(item["values"][0][0])), "f") for item in updates])
            already_applied = _quantity_hash(before) == desired_hash
            serialized_variances = [{**item, "previous_quantity": format(item["previous_quantity"], "f"), "current_quantity": format(item["current_quantity"], "f"), "variance": format(item["variance"], "f")} for item in variances]
            summary = {"status": "dry_run" if dry_run else "planned", "row_count": len(current_records), "valid_count": len(current_records), "changed_count": sum(item["variance"] != 0 for item in variances), "invalid_count": 0, "writes": 0, "plan_hash": plan_hash, "variances": serialized_variances}
            if dry_run: return summary
            reconciliation_id, completed = self._claim_reconciliation(tenant_id, business_date, current.id, previous.id, summary, plan_hash, canonical_hash(value_blocks(before)))
            if completed: return completed
            if not already_applied and updates:
                google.batch_update_values(context.target_file_id, updates)
                summary["writes"] = len(updates)
            verified = google.batch_get_values(context.target_file_id, [item["range"] for item in updates])
            if _quantity_hash(verified) != desired_hash:
                self._fail_reconciliation(reconciliation_id)
                raise DailySheetValidationError("target_verification_failed")
        summary["status"] = "completed"
        with self.session_factory() as session:
            row = session.get(InventoryDailySheetReconciliationModel, reconciliation_id)
            row.status = "completed"; row.target_after_hash = desired_hash
            row.completed_at = inventory_utcnow(); row.summary_json = summary
            row.error_code = row.error_message = None
            session.commit()
        logger.info("inventory_daily_sheet_reconcile_completed", extra={"tenant_id": tenant_id, "business_date": str(business_date), "reconciliation_id": reconciliation_id, "row_count": len(current_records), "changed_count": summary["changed_count"]})
        return summary

    def _reconcile_v2(self, tenant_id: str, business_date: date, context: SheetContext, *, dry_run: bool) -> dict[str, Any]:
        if not dry_run:
            with self.session_factory() as session:
                completed = session.scalar(select(InventoryDailySheetReconciliationModel).where(
                    InventoryDailySheetReconciliationModel.tenant_id == tenant_id,
                    InventoryDailySheetReconciliationModel.business_date == business_date,
                    InventoryDailySheetReconciliationModel.status == "completed",
                ))
                if completed is not None:
                    return dict(completed.summary_json)
        current, previous = self._snapshots(tenant_id, business_date)
        if current is None:
            raise DailySheetValidationError("current_snapshot_incomplete")
        if previous is None:
            if not dry_run:
                self._await_baseline(tenant_id, business_date, current.id)
            return {"status": "awaiting_baseline", "error_code": "missing_previous_snapshot", "writes": 0}
        with self.client_factory(self._token(context.connection_id)) as google:
            current_values = google.batch_get_values(str(current.snapshot_file_id), [context.config.source.a1_range])
            previous_values = google.batch_get_values(str(previous.snapshot_file_id), [context.config.source.a1_range])
            current_records, current_warnings = self._parse_v2_runtime(
                tenant_id, context, current_values[0] if current_values else {}
            )
            previous_records, previous_warnings = self._parse_v2_runtime(
                tenant_id, context, previous_values[0] if previous_values else {}
            )
            with self.session_factory() as registry_session:
                registry = MaterialRegistry(registry_session)
                target_records = dict(current_records)
                current_records, current_semantics, unresolved = self._resolve_material_records(
                    registry, tenant_id, context.working_file_id, str(current.snapshot_file_id),
                    current_records, persist=not dry_run,
                    new_material_policy=context.config.new_material_policy,
                    sheet=context.config.source.sheet,
                )
                previous_records, previous_semantics, _ = self._resolve_material_records(
                    registry, tenant_id, context.working_file_id, str(previous.snapshot_file_id),
                    previous_records, persist=False, use_semantic_matcher=False,
                    new_material_policy="review_required",
                    sheet=context.config.source.sheet,
                )
                if not dry_run:
                    registry_session.commit()
            variances, compare_warnings = build_daily_count_variances(
                current_records, previous_records,
                name_change_policy=context.config.reconciliation.name_change_policy,
            )
            serialized = [{**item,
                "previous_canonical_quantity": format(item["previous_canonical_quantity"], "f") if item["previous_canonical_quantity"] is not None else None,
                "current_canonical_quantity": format(item["current_canonical_quantity"], "f") if item["current_canonical_quantity"] is not None else None,
                "variance": format(item["variance"], "f") if item["variance"] is not None else None,
            } for item in variances]
            summary = {
                "status": "dry_run" if dry_run else "planned", "item_count": len(current_records),
                "row_count": len(current_records), "valid_count": len(current_records), "invalid_count": 0,
                "changed_count": sum(item["variance"] != 0 for item in variances),
                "unit_counts": {unit: sum(record.quantity.canonical_unit == unit for record in current_records.values()) for unit in ("count", "g", "ml")},
                "warnings": current_warnings + previous_warnings + compare_warnings,
                "variances": serialized, "writes": 0,
                "semantic_snapshot": {
                    "current": current_semantics,
                    "previous": previous_semantics,
                    "unresolved_materials": unresolved,
                },
            }
            summary["plan_hash"] = canonical_hash(summary)
            if unresolved and context.config.new_material_policy == "block":
                raise DailyCountSheetValidationError([
                    {"code": "material_resolution_blocked", "item_key": item["sheet_item_key"], "status": item["status"]}
                    for item in unresolved
                ])
            if dry_run:
                return summary
            updates: list[dict[str, Any]] = []
            before: list[dict[str, Any]] = []
            desired_hash = canonical_hash([])
            if context.config.reconciliation.mode == "target_table":
                if unresolved and context.config.new_material_policy != "ignore":
                    raise DailyCountSheetValidationError([
                        {"code": "material_resolution_required", "item_key": item["sheet_item_key"], "status": item["status"]}
                        for item in unresolved
                    ])
                records_for_write = target_records
                if context.config.new_material_policy == "ignore":
                    ignored_keys = {item["sheet_item_key"] for item in unresolved}
                    records_for_write = {
                        key: record for key, record in target_records.items()
                        if record.item_key not in ignored_keys
                    }
                updates = self._daily_count_target_updates(google, context, records_for_write)
                before = google.batch_get_values(context.target_file_id, [item["range"] for item in updates])
                desired_hash = canonical_hash([item["values"][0] for item in updates])
            reconciliation_id, completed = self._claim_reconciliation(
                tenant_id, business_date, current.id, previous.id, summary,
                summary["plan_hash"], canonical_hash(value_blocks(before)),
            )
            if completed:
                return completed
            if updates:
                current_hash = canonical_hash([block.get("values", [[]])[0] if block.get("values") else [] for block in before])
                if current_hash != desired_hash:
                    google.batch_update_values(context.target_file_id, updates)
                    summary["writes"] = len(updates)
                verified = google.batch_get_values(context.target_file_id, [item["range"] for item in updates])
                verified_hash = canonical_hash([block.get("values", [[]])[0] if block.get("values") else [] for block in verified])
                if verified_hash != desired_hash:
                    self._fail_reconciliation(reconciliation_id)
                    raise DailySheetValidationError("target_verification_failed")
        summary["status"] = "completed"
        with self.session_factory() as session:
            row = session.get(InventoryDailySheetReconciliationModel, reconciliation_id)
            row.status = "completed"; row.target_after_hash = desired_hash
            row.completed_at = inventory_utcnow(); row.summary_json = summary
            row.error_code = row.error_message = None
            session.commit()
        return summary

    def _resolve_material_records(
        self, registry: MaterialRegistry, tenant_id: str, source_id: str,
        snapshot_file_id: str, records: dict, *, persist: bool,
        use_semantic_matcher: bool = True,
        new_material_policy: str = "review_required",
        sheet: str,
    ):
        resolved_records: dict = {}
        semantic_records: list[dict[str, Any]] = []
        unresolved: list[dict[str, Any]] = []
        for source_key, record in records.items():
            resolution = registry.resolve(
                tenant_id, source_id=source_id, external_key=record.item_key,
                raw_name=record.item_name, category=record.category,
                source_row=record.source_row, sheet=sheet,
                semantic_matcher=self.material_semantic_matcher if use_semantic_matcher else None,
                context={"source_cells": list(record.source_cells), "raw_quantity": record.quantity.raw},
            )
            semantic = {
                "material_id": resolution.material_id,
                "sheet_item_key": record.item_key,
                "raw_name": record.item_name,
                "category": record.category,
                "raw_quantity": record.quantity.raw,
                "canonical_value": format(record.quantity.canonical_value, "f"),
                "canonical_unit": record.quantity.canonical_unit,
                "spreadsheet_file_id": snapshot_file_id,
                "sheet": sheet,
                "row": record.source_row,
                "source_cells": list(record.source_cells),
                "source_hash": canonical_hash({
                    "file": snapshot_file_id, "row": record.source_row,
                    "name": record.item_name, "quantity": record.quantity.raw,
                }),
                "interpretation_source": resolution.interpretation_source,
                "confidence": float(resolution.confidence),
                "resolution": resolution.to_dict(),
            }
            semantic_records.append(semantic)
            if (
                persist
                and resolution.status == "new_material"
                and resolution.interpretation_source == "gemini"
                and resolution.confidence >= Decimal("0.98")
                and new_material_policy == "auto_register_high_confidence"
            ):
                candidate = registry.queue_candidate(
                    tenant_id, source_id=source_id, external_key=record.item_key,
                    raw_name=record.item_name, category=record.category,
                    source_row=record.source_row, sheet=sheet,
                    resolution=resolution, context=semantic,
                )
                registry.session.flush()
                material = registry.approve(
                    tenant_id, candidate.id, actor_id="policy:auto_register_high_confidence",
                    canonical_name=resolution.suggested_canonical_name,
                    preferred_unit=record.quantity.canonical_unit,
                    canonical_dimension={
                        "count": "count", "g": "mass", "ml": "volume",
                    }.get(record.quantity.canonical_unit, "other"),
                )
                resolution = MaterialResolution(
                    "matched", material.id, record.item_name, material.name,
                    record.category, resolution.confidence,
                    ("auto_registered_high_confidence",), False, "gemini",
                )
                semantic["material_id"] = material.id
                semantic["resolution"] = resolution.to_dict()
            if resolution.requires_review:
                unresolved.append({
                    "status": resolution.status, "material_id": resolution.material_id,
                    "sheet_item_key": record.item_key, "raw_name": record.item_name,
                    "confidence": float(resolution.confidence),
                })
                if persist and new_material_policy in {"review_required", "auto_register_high_confidence"}:
                    registry.queue_candidate(
                        tenant_id, source_id=source_id, external_key=record.item_key,
                        raw_name=record.item_name, category=record.category,
                        source_row=record.source_row, sheet=sheet,
                        resolution=resolution, context=semantic,
                    )
            elif persist and resolution.material_id:
                registry.observe_match(
                    tenant_id, source_id=source_id, external_key=record.item_key,
                    raw_name=record.item_name, material_id=resolution.material_id,
                )
            identity = resolution.material_id or f"unresolved:{record.item_key}"
            target_key = (record.warehouse, identity)
            if target_key in resolved_records:
                unresolved.append({
                    "status": "ambiguous", "material_id": resolution.material_id,
                    "sheet_item_key": record.item_key, "raw_name": record.item_name,
                    "confidence": float(resolution.confidence),
                })
                target_key = source_key
            resolved_records[target_key] = replace(record, item_key=identity)
        return resolved_records, semantic_records, unresolved

    def _daily_count_target_updates(self, google, context: SheetContext, records):
        config = context.config
        blocks = google.batch_get_values(context.target_file_id, [target.item_key_range for target in config.reconciliation.targets])
        targets = {normalize_identifier(target.warehouse): (target, block) for target, block in zip(config.reconciliation.targets, blocks, strict=True)}
        updates: list[dict[str, Any]] = []
        missing: list[str] = []
        for record in records.values():
            pair = targets.get(record.warehouse)
            if pair is None:
                missing.append(record.item_key); continue
            target, block = pair
            match = A1_ROWS.fullmatch(target.item_key_range)
            if match is None:
                raise DailySheetConfigurationError("Target item key range requires explicit row bounds.")
            first_row = int(match.group(2))
            row_map = {str(row[0]).strip(): first_row + offset for offset, row in enumerate(block.get("values") or []) if row}
            row_number = row_map.get(record.item_key)
            if row_number is None:
                missing.append(record.item_key); continue
            sheet_prefix = target.item_key_range.split("!", 1)[0]
            updates.append({"range": f"{sheet_prefix}!{target.quantity_column}{row_number}", "majorDimension": "ROWS", "values": [[format(record.quantity.canonical_value, "f")]]})
            if target.unit_column:
                updates.append({"range": f"{sheet_prefix}!{target.unit_column}{row_number}", "majorDimension": "ROWS", "values": [[record.quantity.canonical_unit]]})
        if missing:
            raise DailyCountSheetValidationError([{"code": "target_item_mapping_missing", "item_key": key} for key in missing])
        return sorted(updates, key=lambda item: item["range"])

    def _snapshots(self, tenant_id, business_date):
        with self.session_factory() as session:
            query = lambda day: session.scalar(select(InventoryDailySheetSnapshotModel).where(InventoryDailySheetSnapshotModel.tenant_id == tenant_id, InventoryDailySheetSnapshotModel.business_date == day, InventoryDailySheetSnapshotModel.status == "completed"))
            current, previous = query(business_date), query(date.fromordinal(business_date.toordinal() - 1))
            if current: session.expunge(current)
            if previous: session.expunge(previous)
            return current, previous

    def _await_baseline(self, tenant_id, business_date, current_id):
        with self.session_factory() as session:
            self._lock(session, f"inventory-sheet-reconcile:{tenant_id}:{business_date}")
            row = session.scalar(select(InventoryDailySheetReconciliationModel).where(InventoryDailySheetReconciliationModel.tenant_id == tenant_id, InventoryDailySheetReconciliationModel.business_date == business_date))
            if row is None:
                session.add(InventoryDailySheetReconciliationModel(tenant_id=tenant_id, business_date=business_date, current_snapshot_id=current_id, previous_snapshot_id=None, status="awaiting_baseline", error_code="missing_previous_snapshot", error_message="Select a completed snapshot as baseline."))
                session.commit()

    def set_baseline(self, tenant_id: str, snapshot_id: str):
        with self.session_factory() as session:
            snapshot = session.scalar(select(InventoryDailySheetSnapshotModel).where(InventoryDailySheetSnapshotModel.tenant_id == tenant_id, InventoryDailySheetSnapshotModel.id == snapshot_id, InventoryDailySheetSnapshotModel.status == "completed"))
            if snapshot is None: raise LookupError("completed_snapshot_not_found")
            self._lock(session, f"inventory-sheet-reconcile:{tenant_id}:{snapshot.business_date}")
            row = session.scalar(select(InventoryDailySheetReconciliationModel).where(
                InventoryDailySheetReconciliationModel.tenant_id == tenant_id,
                InventoryDailySheetReconciliationModel.business_date == snapshot.business_date,
            ))
            if row is None:
                row = InventoryDailySheetReconciliationModel(tenant_id=tenant_id, business_date=snapshot.business_date, current_snapshot_id=snapshot.id, previous_snapshot_id=None)
                session.add(row)
            row.status = "baseline"
            row.summary_json = {"baseline_snapshot_id": snapshot.id}
            row.error_code = row.error_message = None
            session.commit()
            return {"status": "baseline_selected", "snapshot_id": snapshot.id, "business_date": snapshot.business_date}

    def _target_updates(self, google, context, records):
        blocks = google.batch_get_values(context.target_file_id, [item.sku_range for item in context.config.targets])
        by_warehouse = {target.warehouse_key: (target, block) for target, block in zip(context.config.targets, blocks, strict=True)}
        updates, unknown = [], []
        for record in records.values():
            pair = by_warehouse.get(record.warehouse)
            if not pair: unknown.append(f"{record.warehouse}/{record.sku}"); continue
            target, block = pair
            match = A1_ROWS.fullmatch(target.sku_range)
            if not match: raise DailySheetConfigurationError("Target SKU range requires explicit row bounds.")
            start = int(match.group(2))
            mapping = {normalize_sku(row[0]): start + offset for offset, row in enumerate(block.get("values") or []) if row and normalize_sku(row[0])}
            row_number = mapping.get(record.sku)
            if row_number is None:
                if context.config.new_sku_policy == "reject": unknown.append(f"{record.warehouse}/{record.sku}")
                continue
            sheet_prefix = target.sku_range.split("!", 1)[0]
            updates.append({"range": f"{sheet_prefix}!{target.quantity_column}{row_number}", "majorDimension": "ROWS", "values": [[format(record.quantity, "f")]]})
        if unknown: raise DailySheetValidationError("unknown target mapping: " + ", ".join(unknown[:20]))
        return sorted(updates, key=lambda item: item["range"])

    def _claim_reconciliation(self, tenant_id, business_date, current_id, previous_id, summary, plan_hash, before_hash):
        with self.session_factory() as session:
            self._lock(session, f"inventory-sheet-reconcile:{tenant_id}:{business_date}")
            row = session.scalar(select(InventoryDailySheetReconciliationModel).where(InventoryDailySheetReconciliationModel.tenant_id == tenant_id, InventoryDailySheetReconciliationModel.business_date == business_date))
            if row and row.status == "completed": return row.id, dict(row.summary_json)
            updated_at = row.updated_at if row is not None else None
            if updated_at is not None and updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            if row and row.status == "writing" and updated_at and (self.clock() - updated_at).total_seconds() < 900:
                return row.id, {"status": "in_progress", "writes": 0}
            if row is None:
                row = InventoryDailySheetReconciliationModel(tenant_id=tenant_id, business_date=business_date, current_snapshot_id=current_id, previous_snapshot_id=previous_id)
                session.add(row)
            row.current_snapshot_id = current_id
            row.previous_snapshot_id = previous_id
            row.status = "writing"; row.started_at = row.started_at or inventory_utcnow()
            row.row_count = summary["row_count"]; row.valid_count = summary["valid_count"]
            row.changed_count = summary["changed_count"]; row.plan_hash = plan_hash
            row.target_before_hash = before_hash
            session.commit()
            return row.id, None

    def _fail_reconciliation(self, reconciliation_id):
        with self.session_factory() as session:
            row = session.get(InventoryDailySheetReconciliationModel, reconciliation_id)
            row.status = "retryable_failure"; row.error_code = "target_verification_failed"
            row.error_message = "Retry is safe because writes are absolute."
            session.commit()

    def status(self, tenant_id: str):
        with self.session_factory() as session:
            settings = session.scalar(select(InventorySettingsModel).where(InventorySettingsModel.tenant_id == tenant_id))
            snap = session.scalar(select(InventoryDailySheetSnapshotModel).where(
                InventoryDailySheetSnapshotModel.tenant_id == tenant_id,
            ).order_by(InventoryDailySheetSnapshotModel.business_date.desc()))
            rec = session.scalar(select(InventoryDailySheetReconciliationModel).where(
                InventoryDailySheetReconciliationModel.tenant_id == tenant_id,
            ).order_by(InventoryDailySheetReconciliationModel.business_date.desc()))

            is_v4 = bool(
                settings
                and isinstance(settings.daily_sheet_config_json, dict)
                and settings.daily_sheet_config_json.get("version") == 4
            )
            v4_jobs: dict[str, InventoryJobModel | None] = {}
            if is_v4:
                for slot_kind, job_type in (
                    ("snapshot", "inventory_v41_snapshot_slot"),
                    ("reconcile", "inventory_v41_reconcile_slot"),
                ):
                    v4_jobs[slot_kind] = session.scalar(
                        select(InventoryJobModel)
                        .where(
                            InventoryJobModel.tenant_id == tenant_id,
                            InventoryJobModel.job_type == job_type,
                        )
                        .order_by(InventoryJobModel.created_at.desc())
                    )

            timezone_name = settings.timezone if settings else "Asia/Ho_Chi_Minh"
            snapshot_time = settings.daily_snapshot_time_local if settings else "05:50"
            reconcile_time = settings.daily_reconcile_time_local if settings else "07:00"
            local_now = self.clock().astimezone(ZoneInfo(timezone_name))

            def next_run(value: str) -> str:
                hour, minute = (int(item) for item in value.split(":", 1))
                candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if candidate <= local_now:
                    candidate += timedelta(days=1)
                return candidate.isoformat()

            snapshot_status = None
            if snap is not None:
                snapshot_status = {
                    "id": snap.id,
                    "business_date": snap.business_date,
                    "status": snap.status,
                    "snapshot_file_id": snap.snapshot_file_id,
                    "snapshot_url": (
                        f"https://docs.google.com/spreadsheets/d/{snap.snapshot_file_id}/edit"
                        if snap.snapshot_file_id else None
                    ),
                    "archive_folder_url": (
                        f"https://drive.google.com/drive/folders/{snap.archive_folder_id}"
                        if snap.archive_folder_id else None
                    ),
                    "error_code": snap.error_code,
                    "completed_at": snap.reset_completed_at,
                }

            reconciliation_status = None
            if rec is not None:
                reconciliation_status = {
                    "id": rec.id,
                    "business_date": rec.business_date,
                    "previous_business_date": (
                        rec.business_date - timedelta(days=1)
                        if rec.previous_snapshot_id else None
                    ),
                    "status": rec.status,
                    "summary": rec.summary_json,
                    "error_code": rec.error_code,
                    "completed_at": rec.completed_at,
                }

            if is_v4:
                def v4_slot_status(slot_kind: str) -> dict[str, Any] | None:
                    job = v4_jobs.get(slot_kind)
                    if job is None:
                        return None
                    business_date = str((job.payload_json or {}).get("business_date") or "")
                    return {
                        "id": job.id,
                        "business_date": business_date,
                        "status": job.status,
                        "error_code": job.last_error_code,
                        "completed_at": job.completed_at,
                    }

                v4_snapshot = v4_slot_status("snapshot")
                v4_reconciliation = v4_slot_status("reconcile")
                snapshot_status = (
                    None
                    if v4_snapshot is None
                    else {
                        **v4_snapshot,
                        "snapshot_file_id": None,
                        "snapshot_url": None,
                        "archive_folder_url": None,
                    }
                )
                reconciliation_status = (
                    None
                    if v4_reconciliation is None
                    else {
                        **v4_reconciliation,
                        "previous_business_date": None,
                        "summary": {},
                    }
                )

            enabled = bool(settings and settings.daily_sheet_automation_enabled)
            failures = {"retryable_failure", "terminal_failure"}
            degraded = bool(
                (snap is not None and snap.status in failures)
                or (rec is not None and rec.status in failures)
                or any(job is not None and job.status == "failed" for job in v4_jobs.values())
            )
            configured = bool(
                settings
                and settings.daily_working_spreadsheet_file_id
                and settings.daily_archive_root_folder_id
                and settings.daily_sheet_config_json
            )
            return {
                "enabled": enabled,
                "configured": configured,
                "execution_mode": "v4_slots" if is_v4 else "legacy_daily_run",
                "operational_state": "disabled" if not enabled else ("degraded" if degraded else "healthy"),
                "image_pipeline_enabled": bool(settings and settings.image_pipeline_enabled),
                "timezone": timezone_name,
                "current_local_date": local_now.date().isoformat(),
                "working_business_date": (local_now.date() - timedelta(days=1)).isoformat(),
                "snapshot_time": snapshot_time,
                "reconcile_time": reconcile_time,
                "next_snapshot_at": next_run(snapshot_time),
                "next_reconciliation_at": next_run(reconcile_time),
                "working_spreadsheet_url": (
                    f"https://docs.google.com/spreadsheets/d/{settings.daily_working_spreadsheet_file_id}/edit"
                    if settings and settings.daily_working_spreadsheet_file_id else None
                ),
                "last_snapshot": snapshot_status,
                "last_reconciliation": reconciliation_status,
            }
