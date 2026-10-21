"""Tenant-scoped, READ-ONLY preview for the live Catinat usage report.

Never writes Sheets, certifies physical counts, or changes shift state.
"""
from __future__ import annotations

import asyncio
from datetime import date
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.assets.model import ExternalSourceModel
from app.modules.inventory.daily_sheet.catinat_closing import (
    CatinatClosingLayoutError, SHEET, plan_catinat_missing_closings,
)
from app.modules.inventory.daily_sheet.google_client import GoogleSheetsInventoryClient
from app.modules.inventory.persistence_model import InventorySettingsModel
from app.providers.google.auth import get_connection_access_token


class CatinatPreviewError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _metadata_size(metadata: dict[str, Any]) -> int:
    matches = [value for value in metadata.get("sheets", [])
               if (value.get("properties") or {}).get("title") == SHEET]
    if len(matches) != 1:
        raise CatinatPreviewError("catinat_report_tab_missing")
    rows = (matches[0].get("properties") or {}).get("gridProperties") or {}
    row_count = int(rows.get("rowCount") or 0)
    if row_count < 6 or row_count > 500:
        raise CatinatPreviewError("catinat_report_size_unverified")
    return row_count


def serialize_preview(rows: list[list[Any]], business_date: date) -> dict[str, Any]:
    try:
        result = plan_catinat_missing_closings(rows, business_date=business_date)
    except CatinatClosingLayoutError as exc:
        raise CatinatPreviewError(str(exc)) from exc
    return {
        "business_date": result.business_date.isoformat(),
        "mode": "read_only_unverified",
        "physical_count_confirmed": False,
        "auto_fill_allowed": False,
        "suggestions": [
            {"item": s.item, "row": s.row, "target": s.target, "expected_stock": s.computed_value,
             "evidence_hash": s.evidence_hash, "status": s.status}
            for s in result.suggestions
        ],
        "blocked": [{"item": b.item, "row": b.row, "reason": b.reason} for b in result.blocked],
    }


def load_catinat_preview(
    tenant_id: str, business_date: date, *,
    sessions: sessionmaker[Session],
    client_factory: Callable[..., Any] = GoogleSheetsInventoryClient,
    token_resolver: Callable[..., Any] = get_connection_access_token,
) -> dict[str, Any]:
    with sessions() as session:
        settings = session.scalar(select(InventorySettingsModel).where(
            InventorySettingsModel.tenant_id == tenant_id
        ))
        if settings is None or not settings.daily_working_spreadsheet_file_id:
            raise CatinatPreviewError("catinat_workbook_not_configured")
        source = session.scalar(select(ExternalSourceModel).where(
            ExternalSourceModel.tenant_id == tenant_id,
            ExternalSourceModel.id == settings.external_source_id,
        ))
        oauth_id = str(((source.source_metadata or {}) if source else {}).get("oauth_connection_id") or "")
        if not oauth_id:
            raise CatinatPreviewError("catinat_google_connection_missing")
        spreadsheet_id = str(settings.daily_working_spreadsheet_file_id)
    token = token_resolver(oauth_id)
    if hasattr(token, "__await__"):
        token = asyncio.run(token)
    with client_factory(str(token)) as google:
        count = _metadata_size(google.spreadsheet_metadata(spreadsheet_id))
        blocks = google.batch_get_values(
            spreadsheet_id, [f"'{SHEET}'!A1:K{count}"],
            value_render_option="FORMATTED_VALUE",
        )
    if len(blocks) != 1:
        raise CatinatPreviewError("catinat_report_read_incomplete")
    values = list(blocks[0].get("values") or [])
    if len(values) < 6:
        raise CatinatPreviewError("catinat_report_read_incomplete")
    # Google Sheets skips trailing empty cells. Missing mandatory evidence
    # stays missing, never coerced to 0.
    return serialize_preview(values, business_date)
