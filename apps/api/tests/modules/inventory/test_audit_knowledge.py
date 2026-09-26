from pathlib import Path
import tempfile
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.modules.auth_persistence.model import TenantModel
from app.modules.inventory.daily_sheet.audit import InventoryOperationAuditService
from app.modules.inventory.jobs.model import InventoryJobModel
from app.modules.inventory.daily_sheet.knowledge import InventoryKnowledgeError, InventoryKnowledgeService
from app.modules.inventory.daily_sheet.agent_v4.tools import V4WorkbookToolHost, V4AgentSafetyError


def db():
    temp = tempfile.TemporaryDirectory()
    engine = create_engine(f"sqlite:///{Path(temp.name)/'db.sqlite'}")
    event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    for name in (
        "tenants",
        "inventory_knowledge_entries",
        "inventory_daily_carry_forwards",
        "inventory_jobs",
        "inventory_operation_audits",
        "inventory_operation_changes",
    ):
        Base.metadata.tables[name].create(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions.begin() as session:
        session.add(TenantModel(id="tenant-a", name="A", slug="a"))
    return temp, engine, sessions


def test_knowledge_is_versioned_and_only_active_entries_enter_snapshot():
    temp, engine, sessions = db()
    service = InventoryKnowledgeService(sessions)
    draft = service.create(
        "tenant-a",
        kind="RULE",
        title="Closing stock",
        content="Closing = opening + incoming - outgoing",
        structured_rule={"formula": "closing=opening+incoming-outgoing"},
        actor_id="user-a",
    )
    assert draft["status"] == "draft"
    active = service.activate("tenant-a", draft["id"], "user-a")
    assert active["status"] == "active"
    snapshot = service.active_snapshot("tenant-a")
    assert snapshot["count"] == 1
    assert snapshot["entries"][0]["title"] == "Closing stock"
    assert len(snapshot["hash"]) == 64

    revision = service.revise(
        "tenant-a",
        active["id"],
        content="Closing = opening + incoming - outgoing; blank is not zero",
        actor_id="user-a",
    )
    assert revision["version"] == 2 and revision["status"] == "draft"
    assert service.active_snapshot("tenant-a")["entries"][0]["version"] == 1
    service.activate("tenant-a", revision["id"], "user-a")
    rows = service.list("tenant-a")
    versions = {(row["version"], row["status"]) for row in rows}
    assert (1, "archived") in versions and (2, "active") in versions
    engine.dispose(); temp.cleanup()


def test_active_knowledge_budget_blocks_oversized_prompt_before_gemini():
    temp, engine, sessions = db()
    service = InventoryKnowledgeService(sessions)
    active_ids = []
    for index in range(4):
        draft = service.create(
            "tenant-a",
            kind="BUSINESS_NOTE",
            title=f"Large rule {index}",
            content=("x" * 17_000),
            actor_id="user-a",
        )
        active_ids.append(draft["id"])
    for entry_id in active_ids[:3]:
        service.activate("tenant-a", entry_id, "user-a")
    with pytest.raises(InventoryKnowledgeError, match="inventory_knowledge_snapshot_too_large"):
        service.activate("tenant-a", active_ids[3], "user-a")
    assert service.active_snapshot("tenant-a")["count"] == 3
    engine.dispose(); temp.cleanup()


def test_active_knowledge_snapshot_filters_to_relevant_workbook_and_sheet_scopes():
    temp, engine, sessions = db()
    service = InventoryKnowledgeService(sessions)
    entries = [
        ("global", "*", "Global rule"),
        ("workbook", "sheet-a", "Workbook A"),
        ("workbook", "sheet-b", "Workbook B"),
        ("sheet", "Kho", "Kho rule"),
        ("material", "material-x", "Material X"),
    ]
    created = []
    for scope_type, scope_key, title in entries:
        row = service.create(
            "tenant-a",
            kind="RULE",
            title=title,
            content=title + " content",
            scope_type=scope_type,
            scope_key=scope_key,
            actor_id="user-a",
        )
        created.append(service.activate("tenant-a", row["id"], "user-a"))
    snapshot = service.active_snapshot(
        "tenant-a",
        workbook_keys=("sheet-a",),
        sheet_keys=("Kho",),
    )
    assert {entry["title"] for entry in snapshot["entries"]} == {
        "Global rule",
        "Workbook A",
        "Kho rule",
    }
    assert "Workbook B" not in {entry["title"] for entry in snapshot["entries"]}
    assert "Material X" not in {entry["title"] for entry in snapshot["entries"]}
    engine.dispose(); temp.cleanup()


def test_gemini_proposals_are_deduplicated_and_never_auto_activated():
    temp, engine, sessions = db()
    service = InventoryKnowledgeService(sessions)
    proposal = {
        "kind": "DO_NOT_EDIT",
        "title": "Do not edit TOTAL",
        "content": "Rows labeled TOTAL are summary rows.",
        "scope_type": "workbook",
        "scope_key": "*",
        "structured_rule": {"row_label": "TOTAL"},
        "evidence": [{"sheet": "Kho", "cell": "A20", "evidence_hash": "a"*64}],
        "confidence": 0.95,
    }
    first = service.persist_proposals("tenant-a", run_id="run-1", proposals=[proposal])
    second = service.persist_proposals("tenant-a", run_id="run-1", proposals=[proposal])
    assert first[0]["id"] == second[0]["id"]
    assert first[0]["status"] == "proposed"
    assert service.active_snapshot("tenant-a")["count"] == 0
    engine.dispose(); temp.cleanup()


def test_operation_audit_persists_before_after_and_provenance():
    temp, engine, sessions = db()
    result = SimpleNamespace(
        run_id="r"*64,
        status="completed",
        tool_rounds=4,
        read_calls=2,
        read_cells=12,
        writes=1,
        plan_hash="p"*64,
        staged=SimpleNamespace(operations=[1], issues=[], material_actions=[], summary="Updated opening"),
        assessment={"summary": "Grounded"},
        tool_trace=[{"tool": "read_range", "range": "'Kho'!A1:G10", "cells": 70}],
        ranges_read=["'Kho'!A1:G10"],
        business_prompt_source="custom",
        business_prompt_version="custom-v2",
        business_prompt_hash="h"*64,
        knowledge_hash="k"*64,
        knowledge_version=3,
        model="gemini-test",
        execution={"verification_status": "verified"},
        knowledge_proposals=[],
        change_audit=[{
            "sheet": "Kho", "cell": "G17", "row_number": 17,
            "before": 0, "after": 12.5,
            "source_sheet": "Kho", "source_cell": "K17",
            "material_id": "cotton", "warehouse_id": "wh-1",
            "operation_type": "set_cell", "reason": "carry closing to opening",
            "provenance": "exact_copy", "evidence": [],
            "verification_status": "verified",
        }],
    )
    audit = InventoryOperationAuditService(sessions).persist_v4_result(
        "tenant-a", date(2026, 9, 21), stage="evening_reconcile", result=result
    )
    assert audit["summary"]["read_cells"] == 12
    assert audit["knowledge"]["version"] == 3
    assert audit["changes"][0]["before"] == 0
    assert audit["changes"][0]["after"] == 12.5
    assert audit["changes"][0]["source_cell"] == "K17"
    assert audit["changes"][0]["verification_status"] == "verified"
    engine.dispose(); temp.cleanup()


class MinimalGoogle:
    def validate_native_spreadsheet(self, file_id):
        return {"id": file_id, "name": "Inventory", "modifiedTime": "2026-09-22T00:00:00Z"}
    def batch_get_values(self, *_args, **_kwargs):
        return [{"range": "'Kho'!A1", "values": [["TOTAL"]]}]
    def spreadsheet_metadata(self, _):
        return {"properties": {"title": "Inventory", "timeZone": "Asia/Ho_Chi_Minh"}, "sheets": [{"properties": {"title": "Kho", "sheetId": 1, "gridProperties": {"rowCount": 10, "columnCount": 10}}, "merges": [], "protectedRanges": []}]}


class EmptySession:
    def __enter__(self): return self
    def __exit__(self, *_): return None
    def scalars(self, *_): return []
    def scalar(self, *_): return None


def test_knowledge_proposal_requires_previously_read_grounded_evidence():
    host = V4WorkbookToolHost(
        tenant_id="tenant-a", spreadsheet_file_id="sheet-1", allowed_sheets=["Kho"],
        google=MinimalGoogle(), session_factory=lambda: EmptySession(),
        max_read_calls=10, max_read_cells=100, max_edit_operations=10,
    )
    cells = host.read_range({"sheet": "Kho", "a1_range": "A1"})["cells"]
    evidence = {"sheet": "Kho", "cell": "A1", "evidence_hash": cells[0]["evidence_hash"]}
    accepted = host.propose_knowledge({
        "kind": "DO_NOT_EDIT", "title": "TOTAL summary row",
        "content": "Do not edit rows explicitly labeled TOTAL.",
        "evidence": [evidence], "confidence": 0.9,
    })
    assert accepted["accepted"] is True
    with pytest.raises(V4AgentSafetyError, match="missing_or_invalid_evidence"):
        host.propose_knowledge({
            "kind": "RULE", "title": "Ungrounded", "content": "Never guess.",
            "evidence": [{"sheet": "Kho", "cell": "B9", "evidence_hash": "x"*64}],
            "confidence": 0.5,
        })


def test_failure_audit_preserves_partial_gemini_activity():
    temp, engine, sessions = db()
    error = RuntimeError("inventory_gemini_invalid_request")
    error.code = "inventory_gemini_invalid_request"
    error.inventory_audit_context = {
        "tool_rounds": 3,
        "read_calls": 2,
        "read_cells": 42,
        "tool_trace": [
            {"tool": "get_workbook_metadata"},
            {"tool": "read_range", "range": "'Kho'!A1:G6", "cells": 42},
        ],
        "ranges_read": ["'Kho'!A1:G6"],
        "assessment": {"summary": "Observed workbook before provider rejection."},
        "operation_count": 1,
        "issue_count": 0,
        "staged_summary": "One proposed edit had been staged.",
        "change_audit": [{
            "sheet": "Kho",
            "cell": "G3",
            "row_number": 3,
            "before": 10,
            "after": 12,
            "operation_type": "set_cell",
            "reason": "grounded adjustment",
            "provenance": "transformed",
            "evidence": [],
            "verification_status": "failed",
        }],
        "prompt_source": "custom",
        "prompt_version": "custom-v4",
        "prompt_hash": "p"*64,
        "knowledge_hash": "k"*64,
        "knowledge_version": 7,
        "knowledge_proposal_count": 1,
        "model": "gemini-test",
    }
    audit = InventoryOperationAuditService(sessions).persist_failure(
        "tenant-a",
        date(2026, 9, 21),
        stage="evening_reconcile",
        error=error,
    )
    assert audit["status"] == "failed"
    assert audit["error_code"] == "inventory_gemini_invalid_request"
    assert audit["summary"]["read_cells"] == 42
    assert audit["read_ranges"] == ["'Kho'!A1:G6"]
    assert audit["tool_trace"][1]["tool"] == "read_range"
    assert audit["knowledge"]["version"] == 7
    assert audit["changes"][0]["cell"] == "G3"
    assert audit["changes"][0]["verification_status"] == "failed"
    engine.dispose(); temp.cleanup()


def test_morning_reset_detail_falls_back_to_scheduler_job_error():
    temp, engine, sessions = db()
    with sessions.begin() as session:
        session.add(
            InventoryJobModel(
                tenant_id="tenant-a",
                job_type="inventory_v5_morning_reset_slot",
                entity_type="inventory_v5_scheduler_slot",
                entity_id="2026-09-26:morning_reset",
                idempotency_key="inventory-v5-slot:tenant-a:2026-09-26:morning_reset",
                status="failed",
                attempt_count=5,
                max_attempts=5,
                last_error_code="previous_day_gemini_not_verified",
                last_error_message="previous_day_gemini_not_verified",
            )
        )
    detail = InventoryOperationAuditService(sessions).stage_detail(
        "tenant-a", date(2026, 9, 26), "morning_reset"
    )
    assert detail["source"] == "scheduler_job"
    assert detail["status"] == "failed"
    assert detail["error_code"] == "previous_day_gemini_not_verified"
    assert detail["summary"]["attempt_count"] == 5
    engine.dispose(); temp.cleanup()
