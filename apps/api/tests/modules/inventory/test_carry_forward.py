from datetime import date, datetime, timezone
from pathlib import Path
import tempfile

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.modules.assets.model import ExternalSourceModel
from app.modules.auth_persistence.model import TenantModel
from app.modules.inventory.daily.carry_forward import (
    CARRY_FORWARD_PROMPT,
    CarryForwardPlan,
    CarryForwardReviewRequired,
    CarryForwardStaleEvidence,
    InventorySharedCarryForwardService,
)
from app.modules.inventory.daily.carry_forward_planner import CarryForwardToolHost, function_declarations
from app.modules.inventory.daily_sheet.parser import canonical_hash
from app.modules.inventory.persistence_model import (
    InventoryDailyCarryForwardModel,
    InventoryDailySheetSnapshotModel,
    InventoryItemAliasModel,
    InventoryItemModel,
    InventoryLocationModel,
    InventoryMaterialCandidateModel,
    InventorySettingsModel,
)


class Google:
    def __init__(self, source_values=None, target_values=None, protected=False):
        self.source_values = source_values or {"H14": 50, "H15": 35, "H16": 25}
        self.target_values = target_values or {"B14": 1, "B15": 1, "B16": 1}
        self.writes = []
        self.clears = []
        self.batch_get_calls = []
        self.protected = protected

    def __enter__(self): return self
    def __exit__(self, *_args): return None
    def validate_native_spreadsheet(self, file_id):
        return {"id": file_id, "mimeType": "application/vnd.google-apps.spreadsheet", "capabilities": {"canEdit": file_id == "shared"}}
    def spreadsheet_metadata(self, _file_id):
        sheets = []
        for index, title in enumerate(["One", "Two", "Three", "Warehouses"]):
            item = {"properties": {"sheetId": index + 1, "title": title}}
            if index == 3 and self.protected:
                item["protectedRanges"] = [{"range": {"sheetId": 4, "startRowIndex": 13, "endRowIndex": 14, "startColumnIndex": 1, "endColumnIndex": 2}}]
            sheets.append(item)
        return {"sheets": sheets}
    def batch_get_values(self, file_id, ranges, *, value_render_option="UNFORMATTED_VALUE"):
        self.batch_get_calls.append((file_id, tuple(ranges), value_render_option))
        result = []
        for value in ranges:
            cell = value.rsplit("!", 1)[-1]
            values = self.source_values if file_id == "gemini" else self.target_values
            raw = "" if value_render_option == "FORMULA" else values.get(cell)
            result.append({"range": value, "values": [] if raw is None else [[raw]]})
        return result
    def batch_update_values(self, file_id, updates):
        assert file_id == "shared"
        self.writes.extend(updates)
        for update in updates:
            self.target_values[update["range"].rsplit("!", 1)[-1]] = update["values"][0][0]
    def batch_clear_values(self, file_id, ranges):
        assert file_id == "shared"
        self.clears.extend(ranges)
        for value in ranges:
            self.target_values[value.rsplit("!", 1)[-1]] = None


class Planner:
    def __init__(self, rows, issues=None):
        self.rows = rows
        self.issues = list(issues or [])
        self.last_prompt = None
    def plan(self, **kwargs):
        self.last_prompt = kwargs.get("prompt")
        return CarryForwardPlan(
            self.rows,
            self.issues,
            {"sheetId": 4, "title": "Warehouses", "index": 3},
        )


def row(material_id, warehouse_id, source_cell, target_cell, closing, target_before):
    return {
        "material_id": material_id, "warehouse_id": warehouse_id,
        "source": {"spreadsheet_file_id": "gemini", "sheet": "Warehouses", "cell": source_cell, "closing_value": closing, "evidence_hash": canonical_hash([closing])},
        "target": {"spreadsheet_file_id": "shared", "sheet": "Warehouses", "cell": target_cell, "opening_value": closing, "evidence_hash": canonical_hash([target_before])},
    }


def make_db():
    temp = tempfile.TemporaryDirectory()
    engine = create_engine(f"sqlite:///{Path(temp.name) / 'carry.db'}")
    event.listen(engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
    for name in ("tenants", "oauth_connections", "external_sources", "inventory_settings", "inventory_daily_sheet_snapshots", "inventory_daily_carry_forwards", "inventory_prompt_versions", "inventory_items", "inventory_item_aliases", "inventory_locations", "inventory_material_candidates"):
        Base.metadata.tables[name].create(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions.begin() as session:
        session.add_all([TenantModel(id="tenant-a", name="A", slug="a"), TenantModel(id="tenant-b", name="B", slug="b")])
        session.add(ExternalSourceModel(id="source-a", tenant_id="tenant-a", source_key="a", source_type="google_drive", source_metadata={"oauth_connection_id": "connection"}))
        session.add(InventorySettingsModel(tenant_id="tenant-a", external_source_id="source-a", inbox_folder_id="inbox", enabled=True, daily_sheet_automation_enabled=True, daily_working_spreadsheet_file_id="shared", daily_sheet_config_json={"version": 4}))
        session.add(InventoryDailySheetSnapshotModel(id="snapshot-a", tenant_id="tenant-a", business_date=date(2030, 8, 9), external_source_id="source-a", source_spreadsheet_file_id="shared", snapshot_file_id="snapshot", gemini_file_id="gemini", status="completed", gemini_reconcile_status="completed", gemini_reconcile_verified_at=datetime.now(timezone.utc)))
        for number, value in enumerate(("a", "b", "c"), 1):
            session.add(InventoryItemModel(id=f"material-{value}", tenant_id="tenant-a", sku=value, name=value, base_unit="unit"))
            session.add(InventoryLocationModel(id=f"warehouse-{value}", tenant_id="tenant-a", code=value, name=value))
    return temp, engine, sessions


def test_dependency_failure_is_persisted_before_context_resolution():
    temp, engine, sessions = make_db()
    with sessions.begin() as session:
        snapshot = session.scalar(
            select(InventoryDailySheetSnapshotModel).where(
                InventoryDailySheetSnapshotModel.tenant_id == "tenant-a"
            )
        )
        snapshot.gemini_reconcile_status = "failed"
        snapshot.gemini_reconcile_verified_at = None
    service = InventorySharedCarryForwardService(
        sessions,
        client_factory=lambda _token: Google(),
        token_resolver=lambda _id: "token",
        planner=Planner([]),
    )

    result = service.run("tenant-a", date(2030, 8, 10))

    assert result.status == "retryable_failure"
    assert result.error_code == "previous_day_gemini_not_verified"
    with sessions() as session:
        persisted = session.scalar(
            select(InventoryDailyCarryForwardModel).where(
                InventoryDailyCarryForwardModel.target_business_date
                == date(2030, 8, 10)
            )
        )
        assert persisted is not None
        assert persisted.previous_business_date == date(2030, 8, 9)
        assert persisted.error_code == "previous_day_gemini_not_verified"
    engine.dispose(); temp.cleanup()


def test_carry_forward_preserves_each_warehouse_and_writes_shared_only():
    temp, engine, sessions = make_db()
    google = Google()
    rows = [row("material-a", "warehouse-a", "H14", "B14", 50, 1), row("material-b", "warehouse-b", "H15", "B15", 35, 1), row("material-c", "warehouse-c", "H16", "B16", 25, 1)]
    result = InventorySharedCarryForwardService(sessions, client_factory=lambda _token: google, token_resolver=lambda _id: "token", planner=Planner(rows)).run("tenant-a", date(2030, 8, 10))
    assert result.status == "completed"
    assert result.idempotency_key == "inventory-shared-carry-forward:v1:tenant-a:2030-08-10"
    assert google.target_values == {"B14": 50, "B15": 35, "B16": 25}
    assert len(google.writes) == 3
    engine.dispose(); temp.cleanup()


def test_manual_recovery_preview_never_writes_or_clears_and_apply_sets_only_safe_opening_rows():
    temp, engine, sessions = make_db()
    google = Google(
        source_values={"H14": 50},
        target_values={"B14": 1, "C14": 7},
    )
    rows = [
        row("material-a", "warehouse-a", "H14", "B14", 50, 1),
        {
            "type": "clear_cell",
            "semantic_context": {"role": "daily_movement"},
            "target": {
                "spreadsheet_file_id": "shared",
                "sheet": "Warehouses",
                "cell": "C14",
                "evidence_hash": canonical_hash([7]),
            },
        },
    ]
    planner = Planner(rows)
    service = InventorySharedCarryForwardService(
        sessions,
        client_factory=lambda _token: google,
        token_resolver=lambda _id: "token",
        planner=planner,
    )

    preview = service.preview_manual_recovery("tenant-a", date(2030, 8, 10))

    assert preview["status"] == "preview_ready"
    assert "MANUAL RECOVERY MODE" in planner.last_prompt
    assert "Do NOT plan clear_cell operations" in planner.last_prompt
    assert "backend derives the authoritative write value" in planner.last_prompt
    assert preview["safe_operation_count"] == 1
    assert preview["write_operation_count"] == 1
    assert preview["excluded_clear_count"] == 1
    assert google.writes == []
    assert google.clears == []
    assert google.target_values["B14"] == 1
    assert google.target_values["C14"] == 7

    applied = service.apply_manual_recovery(
        "tenant-a",
        date(2030, 8, 10),
        plan_hash=preview["plan_hash"],
    )

    assert applied["status"] == "completed"
    assert applied["applied_count"] == 1
    assert applied["excluded_clear_count"] == 1
    assert google.target_values["B14"] == 50
    assert google.target_values["C14"] == 7
    assert google.clears == []
    engine.dispose(); temp.cleanup()



def test_manual_recovery_queues_evidence_backed_missing_material_for_review():
    temp, engine, sessions = make_db()
    google = Google(
        source_values={"A14": "New Cotton", "H14": 50},
        target_values={"B14": 1},
    )
    issue = {
        "code": "MISSING_CATALOG_ITEMS",
        "message": "A source material has no canonical catalog match.",
        "missing_materials": [{
            "raw_name": "New Cotton",
            "category": "Fabric",
            "name_evidence": {
                "sheet": "Warehouses",
                "cell": "A14",
                "evidence_hash": canonical_hash(["New Cotton"]),
            },
        }],
    }
    service = InventorySharedCarryForwardService(
        sessions,
        client_factory=lambda _token: google,
        token_resolver=lambda _id: "token",
        planner=Planner([], issues=[issue]),
    )

    try:
        service.preview_manual_recovery("tenant-a", date(2030, 8, 10))
        raise AssertionError("missing catalog material must still require review")
    except CarryForwardReviewRequired as exc:
        assert exc.code == "carry_forward_plan_has_issues"

    with sessions() as session:
        candidates = list(session.scalars(select(InventoryMaterialCandidateModel)))
        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.status == "new_material"
        assert candidate.raw_name == "New Cotton"
        assert candidate.category == "Fabric"
        assert candidate.source_id == "gemini"
        assert candidate.sheet == "Warehouses"
        assert candidate.source_row == 14
        assert candidate.external_key == "carry-forward:Warehouses:A14"
        assert candidate.context_json["origin"] == "carry_forward_missing_catalog_item"
        carry = session.scalar(select(InventoryDailyCarryForwardModel))
        assert carry.plan_json["audit"]["queued_material_candidate_count"] == 1

    assert google.writes == []
    assert google.clears == []
    engine.dispose(); temp.cleanup()


def test_manual_recovery_does_not_queue_unverified_missing_material_evidence():
    temp, engine, sessions = make_db()
    google = Google(
        source_values={"A14": "Actual Name", "H14": 50},
        target_values={"B14": 1},
    )
    issue = {
        "code": "MISSING_CATALOG_ITEMS",
        "message": "Unverified candidate evidence.",
        "missing_materials": [{
            "raw_name": "Invented Name",
            "name_evidence": {
                "sheet": "Warehouses",
                "cell": "A14",
                "evidence_hash": canonical_hash(["Actual Name"]),
            },
        }],
    }
    service = InventorySharedCarryForwardService(
        sessions,
        client_factory=lambda _token: google,
        token_resolver=lambda _id: "token",
        planner=Planner([], issues=[issue]),
    )

    try:
        service.preview_manual_recovery("tenant-a", date(2030, 8, 10))
        raise AssertionError("planner issue must block recovery")
    except CarryForwardReviewRequired:
        pass

    with sessions() as session:
        assert session.scalar(select(InventoryMaterialCandidateModel)) is None
    engine.dispose(); temp.cleanup()


def test_manual_recovery_ignores_only_no_reset_rule_and_keeps_set_validation():
    temp, engine, sessions = make_db()
    google = Google(source_values={"H14": 50}, target_values={"B14": 1})
    service = InventorySharedCarryForwardService(
        sessions,
        client_factory=lambda _token: google,
        token_resolver=lambda _id: "token",
        planner=Planner(
            [row("material-a", "warehouse-a", "H14", "B14", 50, 1)],
            issues=[{
                "code": "NO_RESET_RULE",
                "message": "No explicit rule was found for clearing daily movement inputs.",
            }],
        ),
    )

    preview = service.preview_manual_recovery("tenant-a", date(2030, 8, 10))

    assert preview["status"] == "preview_ready"
    assert preview["safe_operation_count"] == 1
    assert preview["write_operation_count"] == 1
    assert google.writes == []
    assert google.clears == []
    with sessions() as session:
        persisted = session.scalar(
            select(InventoryDailyCarryForwardModel).where(
                InventoryDailyCarryForwardModel.target_business_date
                == date(2030, 8, 10)
            )
        )
        manual = persisted.plan_json["manual_recovery"]
        assert manual["ignored_issue_codes"] == ["NO_RESET_RULE"]
        assert manual["ignored_issue_count"] == 1
        assert persisted.issue_count == 0
    engine.dispose(); temp.cleanup()


def test_manual_recovery_still_blocks_no_reset_rule_when_another_issue_exists():
    temp, engine, sessions = make_db()
    google = Google(source_values={"H14": 50}, target_values={"B14": 1})
    service = InventorySharedCarryForwardService(
        sessions,
        client_factory=lambda _token: google,
        token_resolver=lambda _id: "token",
        planner=Planner(
            [row("material-a", "warehouse-a", "H14", "B14", 50, 1)],
            issues=[
                {"code": "NO_RESET_RULE", "message": "No clear/reset rule."},
                {"code": "STRUCTURAL_AMBIGUITY", "message": "Opening mapping is ambiguous."},
            ],
        ),
    )

    try:
        service.preview_manual_recovery("tenant-a", date(2030, 8, 10))
        raise AssertionError("non-reset planner issues must block manual recovery")
    except CarryForwardReviewRequired as exc:
        assert exc.code == "carry_forward_plan_has_issues"

    assert google.writes == []
    assert google.clears == []
    with sessions() as session:
        persisted = session.scalar(
            select(InventoryDailyCarryForwardModel).where(
                InventoryDailyCarryForwardModel.target_business_date
                == date(2030, 8, 10)
            )
        )
        assert persisted.status == "review_required"
        assert persisted.issue_count == 1
        assert [issue["code"] for issue in persisted.plan_json["issues"]] == [
            "NO_RESET_RULE",
            "STRUCTURAL_AMBIGUITY",
        ]
    engine.dispose(); temp.cleanup()


def test_normal_carry_forward_does_not_ignore_no_reset_rule():
    temp, engine, sessions = make_db()
    google = Google(source_values={"H14": 50}, target_values={"B14": 1})
    planner = Planner(
        [row("material-a", "warehouse-a", "H14", "B14", 50, 1)],
        issues=[{"code": "NO_RESET_RULE", "message": "No clear/reset rule."}],
    )
    service = InventorySharedCarryForwardService(
        sessions,
        client_factory=lambda _token: google,
        token_resolver=lambda _id: "token",
        planner=planner,
    )

    result = service.run("tenant-a", date(2030, 8, 10))

    assert result.status == "review_required"
    assert "MANUAL RECOVERY MODE" not in planner.last_prompt
    assert result.error_code == "carry_forward_plan_has_issues"
    assert google.writes == []
    assert google.clears == []
    engine.dispose(); temp.cleanup()


def test_manual_recovery_apply_rejects_target_drift_after_preview():
    temp, engine, sessions = make_db()
    google = Google(source_values={"H14": 50}, target_values={"B14": 1})
    service = InventorySharedCarryForwardService(
        sessions,
        client_factory=lambda _token: google,
        token_resolver=lambda _id: "token",
        planner=Planner([
            row("material-a", "warehouse-a", "H14", "B14", 50, 1)
        ]),
    )

    preview = service.preview_manual_recovery("tenant-a", date(2030, 8, 10))
    google.target_values["B14"] = 9

    try:
        service.apply_manual_recovery(
            "tenant-a",
            date(2030, 8, 10),
            plan_hash=preview["plan_hash"],
        )
        raise AssertionError("stale recovery preview must not apply")
    except CarryForwardStaleEvidence:
        pass

    assert google.writes == []
    assert google.target_values["B14"] == 9
    with sessions() as session:
        persisted = session.scalar(
            select(InventoryDailyCarryForwardModel).where(
                InventoryDailyCarryForwardModel.target_business_date
                == date(2030, 8, 10)
            )
        )
        assert persisted.status == "retryable_failure"
        assert persisted.error_code == "stale_evidence"
    engine.dispose(); temp.cleanup()


def test_zero_is_carried_but_blank_requires_review_without_write():
    temp, engine, sessions = make_db()
    google = Google(source_values={"H14": 0, "H15": None}, target_values={"B14": 1, "B15": 1})
    rows = [row("material-a", "warehouse-a", "H14", "B14", 0, 1), row("material-b", "warehouse-b", "H15", "B15", "", 1)]
    result = InventorySharedCarryForwardService(sessions, client_factory=lambda _token: google, token_resolver=lambda _id: "token", planner=Planner(rows)).run("tenant-a", date(2030, 8, 10))
    assert result.status == "review_required"
    assert google.writes == []
    engine.dispose(); temp.cleanup()


def test_already_applied_recovery_does_not_duplicate_write():
    temp, engine, sessions = make_db()
    google = Google(source_values={"H14": 50}, target_values={"B14": 50})
    rows = [row("material-a", "warehouse-a", "H14", "B14", 50, 50)]
    service = InventorySharedCarryForwardService(sessions, client_factory=lambda _token: google, token_resolver=lambda _id: "token", planner=Planner(rows))
    assert service.run("tenant-a", date(2030, 8, 10)).status == "completed"
    assert google.writes == []
    assert service.run("tenant-a", date(2030, 8, 10)).status == "completed"
    engine.dispose(); temp.cleanup()


def test_cross_tenant_material_is_rejected_before_shared_write():
    temp, engine, sessions = make_db()
    google = Google(source_values={"H14": 50}, target_values={"B14": 1})
    rows = [row("material-x", "warehouse-a", "H14", "B14", 50, 1)]
    result = InventorySharedCarryForwardService(sessions, client_factory=lambda _token: google, token_resolver=lambda _id: "token", planner=Planner(rows)).run("tenant-a", date(2030, 8, 10))
    assert result.status == "review_required"
    assert google.writes == []
    engine.dispose(); temp.cleanup()


def test_protected_or_formula_target_is_rejected_before_write():
    temp, engine, sessions = make_db()
    google = Google(source_values={"H14": 50}, target_values={"B14": 1}, protected=True)
    rows = [row("material-a", "warehouse-a", "H14", "B14", 50, 1)]
    result = InventorySharedCarryForwardService(sessions, client_factory=lambda _token: google, token_resolver=lambda _id: "token", planner=Planner(rows)).run("tenant-a", date(2030, 8, 10))
    assert result.status == "review_required"
    assert google.writes == []
    engine.dispose(); temp.cleanup()


def test_read_back_mismatch_is_not_completed():
    temp, engine, sessions = make_db()
    class NoopWriterGoogle(Google):
        def batch_update_values(self, file_id, updates):
            assert file_id == "shared"
            self.writes.extend(updates)
    google = NoopWriterGoogle(source_values={"H14": 50}, target_values={"B14": 1})
    rows = [row("material-a", "warehouse-a", "H14", "B14", 50, 1)]
    result = InventorySharedCarryForwardService(sessions, client_factory=lambda _token: google, token_resolver=lambda _id: "token", planner=Planner(rows)).run("tenant-a", date(2030, 8, 10))
    assert result.status == "retryable_failure"
    assert result.error_code == "stale_evidence"
    engine.dispose(); temp.cleanup()


def test_carry_forward_declares_bounded_catalog_search_tools_only():
    declarations = function_declarations()
    names = {item["name"] for item in declarations}
    assert "search_material_catalog" in names
    assert "search_warehouse_catalog" in names
    assert "get_material_catalog" not in names
    assert "get_warehouse_catalog" not in names
    submit = next(item for item in declarations if item["name"] == "submit_carry_forward_plan")
    properties = submit["parameters"]["properties"]
    assert properties["operations"]["items"]["type"] == "object"
    operation = properties["operations"]["items"]["properties"]
    assert "backend derives the exact set_cell write value" in operation["value"]["description"]
    evidence = operation["source"]
    assert "backend derives the desired opening value" in evidence["properties"]["opening_value"]["description"]
    assert "not the target cell's current value" in evidence["properties"]["opening_value"]["description"]
    assert "copy the canonical material_id verbatim" in operation["material_id"]["description"]
    assert "copy the canonical warehouse_id verbatim" in operation["warehouse_id"]["description"]
    assert properties["issues"]["items"]["type"] == "object"
    issue_properties = properties["issues"]["items"]["properties"]
    missing_item = issue_properties["missing_materials"]["items"]
    assert set(missing_item["required"]) == {"raw_name", "name_evidence"}
    assert set(missing_item["properties"]["name_evidence"]["required"]) == {"sheet", "cell", "evidence_hash"}


def test_carry_forward_prompt_contains_explicit_opening_reset_authorization():
    assert "today's opening value equal to yesterday's verified closing value" in CARRY_FORWARD_PROMPT
    assert "formula-free per-day operator inputs" in CARRY_FORWARD_PROMPT
    assert "retryable identity feedback" in CARRY_FORWARD_PROMPT
    assert "correct the canonical ID" in CARRY_FORWARD_PROMPT
    assert "MISSING_CATALOG_ITEMS" in CARRY_FORWARD_PROMPT
    assert "name_evidence" in CARRY_FORWARD_PROMPT


def test_unknown_tool_is_returned_to_gemini_as_recoverable_feedback():
    temp, engine, sessions = make_db()
    host = CarryForwardToolHost(
        tenant_id="tenant-a",
        source_id="gemini",
        target_id="shared",
        google=Google(),
        sessions=sessions,
    )
    result = host.execute("made_up_tool", {})
    assert result["error"] == "unknown_carry_forward_tool"
    assert "submit_carry_forward_plan" in result["allowed_tools"]
    assert host.tool_trace[-1] == {
        "tool": "made_up_tool",
        "status": "rejected_unknown_tool",
    }
    engine.dispose(); temp.cleanup()


def test_carry_forward_tool_host_batches_cell_reads():
    temp, engine, sessions = make_db()
    google = Google()
    host = CarryForwardToolHost(
        tenant_id="tenant-a",
        source_id="gemini",
        target_id="shared",
        google=google,
        sessions=sessions,
    )
    result = host.execute(
        "read_source_cells",
        {"sheet": "Warehouses", "cells": ["H14", "H15", "H16"]},
    )
    assert [item["cell"] for item in result["cells"]] == ["H14", "H15", "H16"]
    assert len(google.batch_get_calls) == 1
    assert google.batch_get_calls[0][1] == (
        "'Warehouses'!H14",
        "'Warehouses'!H15",
        "'Warehouses'!H16",
    )
    engine.dispose(); temp.cleanup()


def test_carry_forward_catalog_search_is_bounded_and_alias_aware():
    temp, engine, sessions = make_db()
    with sessions.begin() as session:
        session.add(
            InventoryItemAliasModel(
                id="alias-a",
                tenant_id="tenant-a",
                item_id="material-a",
                alias="Cotton old",
                normalized_alias="cotton old",
            )
        )
    host = CarryForwardToolHost(
        tenant_id="tenant-a",
        source_id="gemini",
        target_id="shared",
        google=Google(),
        sessions=sessions,
    )
    materials = host.execute(
        "search_material_catalog",
        {"query": "cotton", "limit": 10},
    )
    assert [item["material_id"] for item in materials["materials"]] == ["material-a"]
    assert materials["truncated"] is False

    warehouses = host.execute(
        "search_warehouse_catalog",
        {"query": "b", "limit": 1},
    )
    assert len(warehouses["warehouses"]) == 1
    assert warehouses["limit"] == 1
    engine.dispose(); temp.cleanup()


def test_tool_host_requires_grounded_evidence_and_rejects_conflicting_target():
    temp, engine, sessions = make_db()
    google = Google()
    host = CarryForwardToolHost(tenant_id="tenant-a", source_id="gemini", target_id="shared", google=google, sessions=sessions)
    source = host.execute("read_source_cells", {"sheet": "Warehouses", "cells": ["H14"]})["cells"][0]
    target = host.execute("read_target_cells", {"sheet": "Warehouses", "cells": ["B14"]})["cells"][0]
    payload = {"warehouse_sheet": {"sheetId": 4, "title": "Warehouses", "index": 3}, "issues": [], "rows": [{"material_id": "material-a", "warehouse_id": "warehouse-a", "source": {**source, "closing_value": 50}, "target": {**target, "opening_value": 50}}]}
    assert host.execute("submit_carry_forward_plan", payload)["accepted"] is True
    assert host.plan is not None and host.plan.rows[0]["source"]["spreadsheet_file_id"] == "gemini"
    assert [item["tool"] for item in host.tool_trace] == [
        "read_source_cells",
        "read_target_cells",
        "submit_carry_forward_plan",
    ]
    snapshot = host.audit_snapshot(model="gemini-test", rounds=3)
    assert snapshot["evidence_cell_count"] == 2
    assert snapshot["operation_count"] == 1
    assert snapshot["model"] == "gemini-test"
    assert "raw_value" not in str(snapshot["tool_trace"])
    engine.dispose(); temp.cleanup()


def test_tool_host_derives_write_value_from_verified_source_evidence():
    temp, engine, sessions = make_db()
    google = Google(source_values={"H14": 50}, target_values={"B14": 1})
    host = CarryForwardToolHost(
        tenant_id="tenant-a",
        source_id="gemini",
        target_id="shared",
        google=google,
        sessions=sessions,
    )
    source = host.execute(
        "read_source_cells", {"sheet": "Warehouses", "cells": ["H14"]}
    )["cells"][0]
    target = host.execute(
        "read_target_cells", {"sheet": "Warehouses", "cells": ["B14"]}
    )["cells"][0]

    result = host.execute(
        "submit_carry_forward_plan",
        {
            "issues": [],
            "operations": [
                {
                    "type": "set_cell",
                    "material_id": "material-a",
                    "warehouse_id": "warehouse-a",
                    "value": 1,
                    "source": {**source, "closing_value": 999},
                    "target": {**target, "opening_value": 1},
                }
            ],
        },
    )

    assert result["accepted"] is True
    assert host.plan is not None
    operation = host.plan.rows[0]
    assert operation["value"] == 50
    assert operation["source"]["closing_value"] == 50
    assert operation["target"]["opening_value"] == 50
    engine.dispose(); temp.cleanup()


def test_tool_host_returns_retryable_identity_feedback_and_allows_corrected_resubmit():
    temp, engine, sessions = make_db()
    google = Google(source_values={"H14": 50}, target_values={"B14": 1})
    host = CarryForwardToolHost(
        tenant_id="tenant-a",
        source_id="gemini",
        target_id="shared",
        google=google,
        sessions=sessions,
    )
    source = host.execute(
        "read_source_cells", {"sheet": "Warehouses", "cells": ["H14"]}
    )["cells"][0]
    target = host.execute(
        "read_target_cells", {"sheet": "Warehouses", "cells": ["B14"]}
    )["cells"][0]
    invalid = {
        "issues": [],
        "operations": [{
            "type": "set_cell",
            "material_id": "invented-material",
            "warehouse_id": "warehouse-a",
            "source": source,
            "target": target,
        }],
    }

    rejected = host.execute("submit_carry_forward_plan", invalid)

    assert rejected["accepted"] is False
    assert rejected["error"] == "unknown_material"
    assert rejected["retryable"] is True
    assert "search_material_catalog" in rejected["guidance"]
    assert host.plan is None
    assert host.submitted is False

    catalog = host.execute("search_material_catalog", {"query": "a", "limit": 10})
    canonical_material_id = catalog["materials"][0]["material_id"]
    corrected = {
        **invalid,
        "operations": [{
            **invalid["operations"][0],
            "material_id": canonical_material_id,
        }],
    }
    accepted = host.execute("submit_carry_forward_plan", corrected)

    assert accepted["accepted"] is True
    assert host.plan is not None
    assert host.plan.rows[0]["material_id"] == "material-a"
    assert host.submitted is True
    assert any(
        item.get("status") == "rejected_retryable"
        and item.get("error") == "unknown_material"
        for item in host.tool_trace
    )
    engine.dispose(); temp.cleanup()


def test_tool_host_returns_retryable_unknown_warehouse_feedback():
    temp, engine, sessions = make_db()
    google = Google(source_values={"H14": 50}, target_values={"B14": 1})
    host = CarryForwardToolHost(
        tenant_id="tenant-a",
        source_id="gemini",
        target_id="shared",
        google=google,
        sessions=sessions,
    )
    source = host.execute(
        "read_source_cells", {"sheet": "Warehouses", "cells": ["H14"]}
    )["cells"][0]
    target = host.execute(
        "read_target_cells", {"sheet": "Warehouses", "cells": ["B14"]}
    )["cells"][0]

    rejected = host.execute(
        "submit_carry_forward_plan",
        {
            "issues": [],
            "operations": [{
                "type": "set_cell",
                "material_id": "material-a",
                "warehouse_id": "invented-warehouse",
                "source": source,
                "target": target,
            }],
        },
    )

    assert rejected["accepted"] is False
    assert rejected["error"] == "unknown_warehouse"
    assert rejected["retryable"] is True
    assert "search_warehouse_catalog" in rejected["guidance"]
    assert host.plan is None
    assert host.submitted is False
    engine.dispose(); temp.cleanup()


def test_tool_host_rejects_blank_and_fabricated_or_unread_evidence():
    temp, engine, sessions = make_db()
    host = CarryForwardToolHost(tenant_id="tenant-a", source_id="gemini", target_id="shared", google=Google(source_values={"H14": None}), sessions=sessions)
    source = host.execute("read_source_cells", {"sheet": "Warehouses", "cells": ["H14"]})["cells"][0]
    target = host.execute("read_target_cells", {"sheet": "Warehouses", "cells": ["B14"]})["cells"][0]
    import pytest
    with pytest.raises(Exception, match="missing_closing_evidence"):
        host.execute("submit_carry_forward_plan", {"warehouse_sheet": {}, "issues": [], "rows": [{"material_id": "material-a", "warehouse_id": "warehouse-a", "source": {**source, "closing_value": ""}, "target": {**target, "opening_value": ""}}]})
    engine.dispose(); temp.cleanup()


def test_clear_cell_is_evidence_backed_and_applied_after_sets():
    temp, engine, sessions = make_db()
    google = Google(source_values={"H14": 25}, target_values={"B14": 1, "D14": "/"})
    operations = [
        {**row("material-a", "warehouse-a", "H14", "B14", 25, 1), "type": "set_cell", "value": 25},
        {"type": "clear_cell", "semantic_context": {"role": "daily_entry"}, "target": {"spreadsheet_file_id": "shared", "sheet": "Warehouses", "cell": "D14", "current_value": "/", "evidence_hash": canonical_hash(["/"])}},
    ]
    result = InventorySharedCarryForwardService(sessions, client_factory=lambda _token: google, token_resolver=lambda _id: "token", planner=Planner(operations)).run("tenant-a", date(2030, 8, 10))
    assert result.status == "completed"
    assert google.target_values["B14"] == 25 and google.target_values["D14"] is None
    assert google.clears == ["'Warehouses'!D14"]
    engine.dispose(); temp.cleanup()


def test_clear_formula_or_stale_target_is_blocked_without_clear():
    temp, engine, sessions = make_db()
    google = Google(target_values={"D14": 3})
    operations = [{"type": "clear_cell", "semantic_context": {}, "target": {"spreadsheet_file_id": "shared", "sheet": "Warehouses", "cell": "D14", "current_value": 3, "evidence_hash": canonical_hash([999])}}]
    result = InventorySharedCarryForwardService(sessions, client_factory=lambda _token: google, token_resolver=lambda _id: "token", planner=Planner(operations)).run("tenant-a", date(2030, 8, 10))
    assert result.status == "retryable_failure" and google.clears == []
    engine.dispose(); temp.cleanup()


def test_reset_failure_preserves_specific_gemini_code_and_partial_audit():
    temp, engine, sessions = make_db()
    google = Google()

    class FailingPlanner:
        def plan(self, **_kwargs):
            error = CarryForwardReviewRequired("inventory_gemini_invalid_request")
            error.inventory_carry_forward_audit_context = {
                "tool_rounds": 2,
                "tool_trace": [
                    {"tool": "get_source_workbook_metadata", "role": "previous_gemini", "sheet_count": 4},
                    {"tool": "read_source_range", "role": "previous_gemini", "sheet": "Warehouses", "range": "A1:H20", "cell_count": 160},
                ],
                "read_ranges": [
                    {"role": "previous_gemini", "sheet": "Warehouses", "range": "A1:H20"}
                ],
                "evidence_cell_count": 160,
                "operation_count": 0,
                "issue_count": 0,
                "model": "gemini-test",
            }
            raise error

    result = InventorySharedCarryForwardService(
        sessions,
        client_factory=lambda _token: google,
        token_resolver=lambda _id: "token",
        planner=FailingPlanner(),
    ).run("tenant-a", date(2030, 8, 10))

    assert result.status == "review_required"
    assert result.error_code == "inventory_gemini_invalid_request"
    assert result.plan_json["audit"]["read_ranges"][0]["range"] == "A1:H20"
    assert result.plan_json["audit"]["evidence_cell_count"] == 160
    assert "raw_value" not in str(result.plan_json["audit"]["tool_trace"])
    engine.dispose(); temp.cleanup()


def test_recovery_accepts_already_desired_target_after_interrupted_write():
    temp, engine, sessions = make_db()
    class InterruptedGoogle(Google):
        def batch_update_values(self, file_id, updates):
            self.writes.extend(updates)
    first = InterruptedGoogle(source_values={"H14": 50}, target_values={"B14": 1})
    rows = [row("material-a", "warehouse-a", "H14", "B14", 50, 1)]
    service = InventorySharedCarryForwardService(
        sessions, client_factory=lambda _token: first,
        token_resolver=lambda _id: "token", planner=Planner(rows),
    )
    assert service.run("tenant-a", date(2030, 8, 10)).status == "retryable_failure"
    resumed = Google(source_values={"H14": 50}, target_values={"B14": 50})
    recovered = InventorySharedCarryForwardService(
        sessions, client_factory=lambda _token: resumed,
        token_resolver=lambda _id: "token", planner=Planner([]),
    ).run("tenant-a", date(2030, 8, 10))
    assert recovered.status == "completed"
    assert resumed.writes == []
    engine.dispose(); temp.cleanup()
