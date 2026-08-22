from __future__ import annotations
import asyncio, logging, time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable
from zoneinfo import ZoneInfo
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker
from app.modules.assets.model import ExternalSourceModel
from app.modules.auth_persistence.model import OAuthConnectionModel
from app.modules.inventory.daily_sheet.config import DailySheetConfig, normalize_sku
from app.modules.inventory.daily_sheet.google_client import GoogleSheetsInventoryClient, require_sheets_scope
from app.modules.inventory.daily_sheet.parser import A1_ROWS, DailySheetValidationError, StockRecord, build_variances, canonical_hash, parse_stock_records, value_blocks
from app.modules.inventory.persistence_model import InventoryDailySheetReconciliationModel, InventoryDailySheetSnapshotModel, InventorySettingsModel, inventory_utcnow
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
    config: DailySheetConfig
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
    def __init__(self, session_factory: sessionmaker[Session], *, client_factory: Callable = GoogleSheetsInventoryClient, token_resolver: Callable = get_connection_access_token, clock: Callable = lambda: datetime.now(timezone.utc)):
        self.session_factory = session_factory
        self.client_factory = client_factory
        self.token_resolver = token_resolver
        self.clock = clock

    def _lock(self, session: Session, key: str) -> None:
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": key})

    def _context(self, tenant_id: str, *, require_enabled: bool = True) -> SheetContext:
        with self.session_factory() as session:
            settings = session.scalar(select(InventorySettingsModel).where(InventorySettingsModel.tenant_id == tenant_id, InventorySettingsModel.enabled.is_(True)))
            if settings is None or (require_enabled and not settings.daily_sheet_automation_enabled):
                raise DailySheetConfigurationError("Daily Google Sheet automation is disabled.")
            if not settings.daily_working_spreadsheet_file_id or not settings.daily_archive_root_folder_id or not settings.daily_sheet_config_json:
                raise DailySheetConfigurationError("Daily Google Sheet configuration is incomplete.")
            try:
                config = DailySheetConfig.model_validate(settings.daily_sheet_config_json)
            except Exception as exc:
                raise DailySheetConfigurationError("Daily Google Sheet mapping is invalid.") from exc
            if config.reset.mode == "restore_template" and not settings.daily_template_spreadsheet_file_id:
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
                str(settings.daily_archive_root_folder_id),
                settings.daily_template_spreadsheet_file_id,
                settings.daily_target_spreadsheet_file_id or str(settings.daily_working_spreadsheet_file_id),
                config, tuple(connection.scopes_json or ()),
            )
        require_sheets_scope(list(context.scopes))
        return context

    def _token(self, connection_id: str) -> str:
        value = self.token_resolver(connection_id)
        return asyncio.run(value) if hasattr(value, "__await__") else str(value)

    def validate_configuration(self, tenant_id: str) -> dict[str, Any]:
        try:
            context = self._context(tenant_id, require_enabled=False)
            with self.client_factory(self._token(context.connection_id)) as google:
                google.validate_native_spreadsheet(context.working_file_id)
                archive = google.drive_file(context.archive_root_id)
                if archive.get("mimeType") != "application/vnd.google-apps.folder":
                    raise DailySheetConfigurationError("Archive root must be a Google Drive folder.")
                if (archive.get("capabilities") or {}).get("canAddChildren") is False:
                    raise DailySheetConfigurationError("Archive root is not writable.")
                google.spreadsheet_metadata(context.working_file_id)
                source_values = google.batch_get_values(
                    context.working_file_id,
                    [item.a1_range for item in context.config.source_ranges],
                )
                parse_stock_records(context.config, source_values)
                google.validate_native_spreadsheet(context.target_file_id)
                google.batch_get_values(context.target_file_id, [item.sku_range for item in context.config.targets])
                if context.template_file_id:
                    google.validate_native_spreadsheet(context.template_file_id)
                    google.batch_get_values(
                        context.template_file_id,
                        context.config.reset.ranges,
                        value_render_option="FORMULA",
                    )
            return {"valid": True, "checks": [{"code": "configuration", "ok": True}]}
        except Exception as exc:
            return {"valid": False, "checks": [{"code": getattr(exc, "code", "invalid_configuration"), "ok": False, "message": str(exc)}]}

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

    def snapshot_and_reset(self, tenant_id: str, business_date: date):
        context = self._context(tenant_id)
        row, claimed = self._claim_snapshot(tenant_id, business_date, context)
        if not claimed: return row
        started = time.monotonic()
        if row.status == "resetting":
            return self._resume_reset(row.id, tenant_id, business_date, context, started)
        try:
            with self.client_factory(self._token(context.connection_id)) as google:
                source_meta = google.validate_native_spreadsheet(context.working_file_id)
                before = _parse_time(source_meta.get("modifiedTime"))
                source_values = google.batch_get_values(context.working_file_id, [item.a1_range for item in context.config.source_ranges])
                source_hash = canonical_hash(value_blocks(source_values))
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
                snapshot_values = google.batch_get_values(snapshot_id, [item.a1_range for item in context.config.source_ranges])
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

    def reconcile(self, tenant_id: str, business_date: date, *, dry_run: bool = False):
        context = self._context(tenant_id)
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
            current_records = parse_stock_records(context.config, google.batch_get_values(str(current.snapshot_file_id), [item.a1_range for item in context.config.source_ranges]))
            previous_records = parse_stock_records(context.config, google.batch_get_values(str(previous.snapshot_file_id), [item.a1_range for item in context.config.source_ranges]))
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

            enabled = bool(settings and settings.daily_sheet_automation_enabled)
            failures = {"retryable_failure", "terminal_failure"}
            degraded = bool(
                (snap is not None and snap.status in failures)
                or (rec is not None and rec.status in failures)
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
                "operational_state": "disabled" if not enabled else ("degraded" if degraded else "healthy"),
                "image_pipeline_enabled": bool(settings and settings.image_pipeline_enabled),
                "timezone": timezone_name,
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
