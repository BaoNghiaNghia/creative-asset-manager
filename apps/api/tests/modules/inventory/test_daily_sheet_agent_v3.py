from __future__ import annotations

from copy import deepcopy
from datetime import date
import json
from types import SimpleNamespace

import pytest

from app.modules.inventory.daily_sheet.agent.contracts import (
    EditOperation, EditPlan, MaterialAction, PlanIssue, PlanSource,
)
from app.modules.inventory.daily_sheet.agent.executor import (
    EditPlanVerificationError, GoogleSheetEditPlanExecutor, StaleEditPlan, plan_hash,
)
from app.modules.inventory.daily_sheet.agent.guard import SheetAgentSafetyGuard
from app.modules.inventory.daily_sheet.agent.planner import (
    PLANNER_INSTRUCTIONS,
    GeminiSheetAgentPlanner,
    SheetAgentUnavailable,
    bind_authoritative_source,
    build_cell_evidence,
)
from app.modules.inventory.daily_sheet.agent.service import (
    InventoryDailySheetAgentService, SheetAgentApplyNotAllowed,
)
from app.modules.inventory.daily_sheet.agent.snapshot import build_workbook_snapshot
from app.modules.inventory.daily_sheet.config import GeminiSheetAgentConfig, parse_daily_sheet_config


def snapshot(
    *,
    closing="12",
    formula=None,
    modified="2026-08-24T00:00:00Z",
    requested_range="'Daily'!A1:H4",
):
    raw = [
        ["STT", "Material", "Category", "Opening", "Used", "Inbound", "Waste", "Closing"],
        ["1", "Milk", "Dairy", "", "2", "1", "", closing],
        ["", "TOTAL", "", "", "", "", "", ""],
    ]
    formulas = deepcopy(raw)
    if formula is not None:
        formulas[1][3] = formula
    return build_workbook_snapshot(
        spreadsheet_file_id="sheet-1",
        file_metadata={"name": "Daily", "modifiedTime": modified},
        spreadsheet_metadata={
            "properties": {"title": "Daily", "timeZone": "Asia/Ho_Chi_Minh"},
            "sheets": [{
                "properties": {"title": "Daily", "sheetId": 7},
                "merges": [{"sheetId": 7, "startRowIndex": 0, "endRowIndex": 1}],
                "protectedRanges": [{"range": {"sheetId": 7, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 8}, "warningOnly": False}],
            }],
        },
        requested_range=requested_range,
        raw_block={"values": raw},
        formula_block={"values": formulas},
    )


def op(operation_id="carry", *, kind="set_cell", cell="D2", value="12", copy_from="H2", business_action="carry_forward", review=False):
    return EditOperation(
        operation_id=operation_id, type=kind, sheet="Daily", cell=cell,
        value=value if kind == "set_cell" else None,
        business_action=business_action,
        evidence_cells=[copy_from] if copy_from else [],
        copy_from=copy_from, requires_review=review,
    )


def make_plan(book, operations=None, *, status="ready", requires_review=False, material_actions=None):
    return EditPlan(
        status=status, requires_review=requires_review, summary="Daily rollover",
        source=PlanSource(
            spreadsheet_file_id=book.spreadsheet_file_id, source_hash=book.source_hash,
            sheet=book.sheet_title, range=book.requested_range,
        ),
        operations=operations if operations is not None else [op()],
        material_actions=material_actions or [],
    )


def guard_plan(edit_plan, book=None, guard=None, max_operations=200):
    book = book or snapshot()
    return (guard or SheetAgentSafetyGuard()).validate(
        tenant_id="tenant-a", plan=edit_plan, snapshot=book,
        allowed_range="'Daily'!A1:H4", max_operations=max_operations,
        allow_structure_changes=False, allow_formula_changes=False,
    )


def test_v3_config_defaults_to_shadow_and_report_only():
    config = parse_daily_sheet_config({
        "version": 3, "mode": "gemini_sheet_agent",
        "source": {"sheet": "Daily", "range": "A1:H4"},
    })
    assert isinstance(config, GeminiSheetAgentConfig)
    assert config.agent.apply_mode == "shadow"
    assert config.reconciliation.mode == "report_only"
    assert config.source.a1_range == "'Daily'!A1:H4"


def test_v3_invalid_apply_mode_and_unknown_version_fail_closed():
    base = {
        "version": 3,
        "mode": "gemini_sheet_agent",
        "source": {"sheet": "Daily", "range": "A1:H4"},
    }
    with pytest.raises(Exception):
        parse_daily_sheet_config({
            **base,
            "agent": {"apply_mode": "enabled"},
        })
    with pytest.raises(Exception):
        parse_daily_sheet_config({
            **base,
            "version": 99,
        })


def test_snapshot_is_deterministic_and_captures_full_evidence():
    first, second = snapshot(), snapshot()
    assert first.source_hash == second.source_hash
    assert first.coordinates[1][7] == "H2"
    assert first.merged_ranges and first.protected_ranges
    assert first.workbook_timezone == "Asia/Ho_Chi_Minh"


def test_snapshot_hash_changes_with_values_and_modified_time():
    assert snapshot(closing="12").source_hash != snapshot(closing="13").source_hash
    assert snapshot(modified="a").source_hash != snapshot(modified="b").source_hash


def test_guard_accepts_faithful_carry_forward_then_clear():
    book = snapshot()
    result = guard_plan(make_plan(book, [
        op(), op("clear-used", kind="clear_cell", cell="E2", copy_from=None),
    ]), book)
    assert result.accepted and not result.requires_review
    assert [item.operation_id for item in result.set_operations] == ["carry"]
    assert [item.operation_id for item in result.clear_operations] == ["clear-used"]


@pytest.mark.parametrize("field,value,error", [
    ("source_hash", "0" * 64, "source_hash_mismatch"),
    ("spreadsheet_file_id", "other", "spreadsheet_file_mismatch"),
    ("range", "'Daily'!A1:G4", "source_binding_mismatch"),
])
def test_guard_rejects_wrong_source_binding(field, value, error):
    book = snapshot()
    edit_plan = make_plan(book)
    setattr(edit_plan.source, field, value)
    assert error in guard_plan(edit_plan, book).errors


def test_guard_rejects_duplicate_targets():
    book = snapshot()
    result = guard_plan(make_plan(book, [op("a"), op("b")]), book)
    assert any(value.startswith("conflicting_target:") for value in result.errors)


@pytest.mark.parametrize("operation", [
    op("row", kind="insert_row", cell="A2", copy_from=None),
    op("outside", cell="I2", value="1", copy_from=None),
])
def test_guard_rejects_structure_and_out_of_range(operation):
    book = snapshot()
    assert not guard_plan(make_plan(book, [operation]), book).accepted


def test_guard_rejects_operation_limit():
    book = snapshot()
    result = guard_plan(make_plan(book, [op("a"), op("b", cell="D3")]), book, max_operations=1)
    assert "operation_limit_exceeded" in result.errors


def test_guard_blocks_formula_overwrite():
    book = snapshot(formula="=H2")
    assert "formula_change_blocked:carry" in guard_plan(make_plan(book), book).errors


def test_guard_blank_is_not_zero():
    book = snapshot(closing="")
    assert "blank_to_zero:carry" in guard_plan(make_plan(book, [op(value="0")]), book).errors


def test_copy_provenance_preserves_structured_numeric_text_exactly():
    book = snapshot(closing="0014")
    result = guard_plan(make_plan(book, [op(value="14")]), book)
    assert result.accepted
    assert result.requires_review
    assert "transformed_value:carry" in result.review_reasons


def test_coherent_compound_quantity_is_faithfully_copied_without_review():
    raw_quantity = "1(238,5g); 2(299,4g)"
    book = snapshot(closing=raw_quantity)
    result = guard_plan(make_plan(book, [op(value=raw_quantity)]), book)
    assert result.accepted
    assert not result.requires_review


def test_material_action_schema_exposes_grounding_cells():
    properties = MaterialAction.model_json_schema()["properties"]
    assert {"source_key_cell", "source_name", "source_name_cell"} <= properties.keys()


def test_copy_provenance_allows_json_number_for_numeric_sheet_value():
    book = snapshot(closing="14")
    result = guard_plan(make_plan(book, [op(value=14)]), book)
    assert result.accepted
    assert not result.requires_review


def test_guard_requires_review_for_transformation_and_repair():
    book = snapshot()
    transformed = guard_plan(make_plan(book, [op(value="13")]), book)
    repaired = guard_plan(make_plan(book, [op(business_action="data_repair")]), book)
    assert transformed.requires_review and "transformed_value:carry" in transformed.review_reasons
    assert repaired.requires_review and "data_repair:carry" in repaired.review_reasons


def test_guard_requires_review_for_unproven_set():
    book = snapshot()
    result = guard_plan(make_plan(book, [op(copy_from=None)]), book)
    assert result.accepted and result.requires_review
    assert "unproven_set:carry" in result.review_reasons


def test_guard_validates_grounded_tenant_material_match():
    book = snapshot()
    action = MaterialAction(
        action="MATCH_EXISTING",
        source_key="1",
        source_key_cell="A2",
        source_name="Milk",
        source_name_cell="B2",
        material_id="material-1",
    )
    guard = SheetAgentSafetyGuard(
        material_validator=lambda tenant, material: (tenant, material) == ("tenant-a", "material-1")
    )
    result = guard_plan(make_plan(book, [], material_actions=[action]), book, guard)
    assert result.accepted and not result.requires_review

    bad = action.model_copy(update={"material_id": "tenant-b-material"})
    assert "invalid_material_match:1" in guard_plan(
        make_plan(book, [], material_actions=[bad]), book, guard
    ).errors


@pytest.mark.parametrize(
    "update,error",
    [
        ({"source_key": "wrong"}, "material_source_key_mismatch:wrong"),
        ({"source_key_cell": "Z99"}, "material_source_key_cell_invalid:1"),
        ({"source_name": "Wrong"}, "material_source_name_mismatch:1"),
        ({"source_name_cell": "Z99"}, "material_source_name_cell_invalid:1"),
        ({"source_name_cell": None}, "material_source_name_incomplete:1"),
    ],
)
def test_guard_rejects_invalid_material_identity_grounding(update, error):
    book = snapshot()
    action = MaterialAction(
        action="MATCH_EXISTING",
        source_key="1",
        source_key_cell="A2",
        source_name="Milk",
        source_name_cell="B2",
        material_id="material-1",
    ).model_copy(update=update)
    guard = SheetAgentSafetyGuard(material_validator=lambda tenant, material: True)
    assert error in guard_plan(
        make_plan(book, [], material_actions=[action]), book, guard
    ).errors


def test_match_existing_requires_source_key_cell():
    book = snapshot()
    action = MaterialAction(
        action="MATCH_EXISTING", source_key="1", material_id="material-1"
    )
    guard = SheetAgentSafetyGuard(material_validator=lambda tenant, material: True)
    assert "material_source_key_cell_missing:1" in guard_plan(
        make_plan(book, [], material_actions=[action]), book, guard
    ).errors


@pytest.mark.parametrize("action", ["NEW_MATERIAL", "POSSIBLE_RENAME", "AMBIGUOUS"])
def test_guard_routes_grounded_material_changes_to_review(action):
    book = snapshot()
    material = MaterialAction(
        action=action,
        source_key="1",
        source_key_cell="A2",
        source_name="Milk",
        source_name_cell="B2",
    )
    result = guard_plan(make_plan(book, [], material_actions=[material]), book)
    assert result.accepted and result.requires_review
    assert f"material_{action.lower()}:1" in result.review_reasons


@pytest.mark.parametrize("status,accepted,review", [
    ("blocked", False, False), ("review_required", True, True),
])
def test_guard_honors_planner_status(status, accepted, review):
    book = snapshot()
    result = guard_plan(make_plan(book, [], status=status), book)
    assert result.accepted is accepted
    assert result.requires_review is review


class FakeGoogle:
    def __init__(self, values=None, corrupt_set=False):
        self.values = dict(values or {"'Daily'!D2": "", "'Daily'!E2": "2"})
        self.calls = []
        self.corrupt_set = corrupt_set

    def batch_get_values(self, spreadsheet_id, ranges, **kwargs):
        self.calls.append(("get", list(ranges)))
        return [
            {"range": item, "values": [] if self.values.get(item) is None else [[self.values.get(item)]]}
            for item in ranges
        ]

    def batch_update_values(self, spreadsheet_id, updates):
        self.calls.append(("set", deepcopy(updates)))
        for update in updates:
            self.values[update["range"]] = "WRONG" if self.corrupt_set else update["values"][0][0]

    def batch_clear_values(self, spreadsheet_id, ranges):
        self.calls.append(("clear", list(ranges)))
        for item in ranges:
            self.values[item] = None


def execute(book, edit_plan, google, loader=None):
    executor = GoogleSheetEditPlanExecutor(google=google, snapshot_loader=loader or (lambda: book))
    return executor.execute(
        plan=edit_plan, snapshot=book, guard=guard_plan(edit_plan, book),
        expected_plan_hash=plan_hash(edit_plan), expected_source_hash=book.source_hash,
    )


def test_executor_applies_and_verifies_set_before_clear_with_before_state():
    book = snapshot()
    edit_plan = make_plan(book, [op(), op("clear-used", kind="clear_cell", cell="E2", copy_from=None)])
    google = FakeGoogle()
    result = execute(book, edit_plan, google)
    names = [call[0] for call in google.calls]
    assert result.status == "completed" and result.set_count == 1 and result.clear_count == 1
    assert names.index("set") < names.index("clear")
    assert result.before_state == {"'Daily'!D2": "", "'Daily'!E2": "2"}


def test_executor_never_clears_if_set_verification_fails():
    book = snapshot()
    edit_plan = make_plan(book, [op(), op("clear-used", kind="clear_cell", cell="E2", copy_from=None)])
    google = FakeGoogle(corrupt_set=True)
    with pytest.raises(EditPlanVerificationError):
        execute(book, edit_plan, google)
    assert not any(call[0] == "clear" for call in google.calls)


def test_executor_rejects_stale_source_before_write():
    book, changed = snapshot(), snapshot(closing="99")
    google = FakeGoogle()
    with pytest.raises(StaleEditPlan):
        execute(book, make_plan(book), google, loader=lambda: changed)
    assert not any(call[0] in {"set", "clear"} for call in google.calls)


def test_executor_rejects_changed_plan_before_write():
    book, google = snapshot(), FakeGoogle()
    edit_plan = make_plan(book)
    executor = GoogleSheetEditPlanExecutor(google=google, snapshot_loader=lambda: book)
    with pytest.raises(StaleEditPlan):
        executor.execute(
            plan=edit_plan, snapshot=book, guard=guard_plan(edit_plan, book),
            expected_plan_hash="0" * 64, expected_source_hash=book.source_hash,
        )
    assert not any(call[0] in {"set", "clear"} for call in google.calls)


def test_executor_accepts_safe_numeric_equivalence():
    book = snapshot(closing="12.0")
    assert execute(book, make_plan(book, [op(value=12)]), FakeGoogle()).verification_status == "verified"


class FakePlanner:
    def __init__(self, edit_plan):
        self.edit_plan = edit_plan
    def plan(self, **kwargs):
        return self.edit_plan, "gemini-test"


class FakeExecutor:
    def __init__(self):
        self.calls = []
    def execute(self, **kwargs):
        from app.modules.inventory.daily_sheet.agent.contracts import ExecutionResult
        self.calls.append(kwargs)
        return ExecutionResult(
            status="completed", source_hash=kwargs["snapshot"].source_hash,
            plan_hash=kwargs["expected_plan_hash"], set_count=1,
            verification_status="verified",
        )


def config(mode):
    return parse_daily_sheet_config({
        "version": 3, "mode": "gemini_sheet_agent",
        "source": {"sheet": "Daily", "range": "A1:H4"},
        "agent": {"apply_mode": mode},
    })


def agent_service(mode, edit_plan, book, executor):
    return InventoryDailySheetAgentService(
        planner=FakePlanner(edit_plan), snapshot_loader=lambda tenant: book,
        executor_factory=lambda tenant, loader: executor,
        config_loader=lambda tenant: config(mode), guard=SheetAgentSafetyGuard(),
    )


@pytest.mark.parametrize("mode,expected", [("shadow", "shadow"), ("review", "review_required")])
def test_shadow_and_review_modes_never_write(mode, expected):
    book, executor = snapshot(), FakeExecutor()
    result = agent_service(mode, make_plan(book), book, executor).plan_agent_run(
        "tenant-a", date(2026, 8, 24), dry_run=True
    )
    assert result.status == expected and executor.calls == []


def test_shadow_mode_direct_apply_is_blocked_without_writes():
    book, executor = snapshot(), FakeExecutor()
    edit_plan = make_plan(book)
    service = agent_service("shadow", edit_plan, book, executor)

    with pytest.raises(SheetAgentApplyNotAllowed, match="sheet_agent_shadow_mode"):
        service.apply_agent_plan(
            "tenant-a",
            edit_plan,
            expected_plan_hash=plan_hash(edit_plan),
            expected_source_hash=book.source_hash,
        )

    assert executor.calls == []


def test_review_mode_apply_binds_exact_plan_and_source_hash():
    book, executor = snapshot(), FakeExecutor()
    edit_plan = make_plan(book)
    result = agent_service("review", edit_plan, book, executor).apply_agent_plan(
        "tenant-a",
        edit_plan,
        expected_plan_hash=plan_hash(edit_plan),
        expected_source_hash=book.source_hash,
    )
    assert result.status == "completed"
    assert len(executor.calls) == 1


def test_auto_mode_executes_only_ready_guarded_plan():
    book, executor = snapshot(), FakeExecutor()
    result = agent_service("auto", make_plan(book), book, executor).plan_agent_run(
        "tenant-a", date(2026, 8, 24), dry_run=False
    )
    assert result.status == "completed" and len(executor.calls) == 1


def test_auto_mode_does_not_execute_review_plan():
    book, executor = snapshot(), FakeExecutor()
    result = agent_service("auto", make_plan(book, [op(value="13")]), book, executor).plan_agent_run(
        "tenant-a", date(2026, 8, 24), dry_run=False
    )
    assert result.status == "review_required" and executor.calls == []


class FakeSession:
    def __init__(self, control):
        self.control = control
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def scalar(self, statement):
        return self.control
    def scalars(self, statement):
        return []


class FakeGateway:
    def __init__(self, response):
        self.response = response
        self.calls = []
    def analyze_structured_text(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(extracted_json=self.response)


def planner_for(control, response):
    gateway = FakeGateway(response)
    planner = GeminiSheetAgentPlanner(lambda: FakeSession(control), gateway, enabled=True)
    return planner, gateway


def test_planner_fails_closed_when_ai_disabled_or_emergency_stopped():
    book = snapshot()
    response = make_plan(book, []).model_dump(mode="json")
    disabled, _ = planner_for(SimpleNamespace(
        enabled=False, emergency_stop=False, provider="gemini", allowed_models_json=["m"]
    ), response)
    stopped, _ = planner_for(SimpleNamespace(
        enabled=True, emergency_stop=True, provider="gemini", allowed_models_json=["m"]
    ), response)
    with pytest.raises(SheetAgentUnavailable, match="inventory_ai_disabled"):
        disabled.plan(tenant_id="tenant-a", business_date="2026-08-24", snapshot=book, business_goal=["safe"])
    with pytest.raises(SheetAgentUnavailable, match="inventory_ai_emergency_stop"):
        stopped.plan(tenant_id="tenant-a", business_date="2026-08-24", snapshot=book, business_goal=["safe"])


def test_planner_uses_strict_contract_and_does_not_mutate_snapshot():
    book = snapshot()
    before = book.model_dump(mode="json")
    response = make_plan(book, []).model_dump(mode="json")
    planner, gateway = planner_for(SimpleNamespace(
        enabled=True, emergency_stop=False, provider="gemini", allowed_models_json=["gemini-test"]
    ), response)
    result, model = planner.plan(
        tenant_id="tenant-a", business_date="2026-08-24",
        snapshot=book, business_goal=["safe"],
    )
    assert result.status == "ready" and model == "gemini-test"
    assert gateway.calls[0]["schema"]["additionalProperties"] is False
    assert book.model_dump(mode="json") == before

def test_guard_blocks_protected_and_merged_cells():
    book = snapshot()
    book.protected_ranges = ['{"range":{"sheetId":7,"startRowIndex":1,"endRowIndex":2,"startColumnIndex":3,"endColumnIndex":4}}']
    assert "protected_cell:carry" in guard_plan(make_plan(book), book).errors
    book.protected_ranges = []
    book.merged_ranges = ['{"sheetId":7,"startRowIndex":1,"endRowIndex":2,"startColumnIndex":3,"endColumnIndex":4}']
    assert "merged_cell:carry" in guard_plan(make_plan(book), book).errors


def test_edit_plan_contract_rejects_extra_fields_and_null_set_values():
    book = snapshot()
    payload = make_plan(book).model_dump(mode="json")
    payload["unexpected"] = True
    with pytest.raises(Exception):
        EditPlan.model_validate(payload)
    with pytest.raises(Exception):
        EditOperation(
            operation_id="invalid", type="set_cell", sheet="Daily", cell="D2",
            value=None,
        )


def test_planner_failure_produces_no_write():
    book, executor = snapshot(), FakeExecutor()
    class FailingPlanner:
        def plan(self, **kwargs):
            raise RuntimeError("provider_failed")
    service = InventoryDailySheetAgentService(
        planner=FailingPlanner(), snapshot_loader=lambda tenant: book,
        executor_factory=lambda tenant, loader: executor,
        config_loader=lambda tenant: config("auto"), guard=SheetAgentSafetyGuard(),
    )
    with pytest.raises(RuntimeError, match="provider_failed"):
        service.plan_agent_run("tenant-a", date(2026, 8, 24), dry_run=False)
    assert executor.calls == []


def _enabled_control():
    return SimpleNamespace(
        enabled=True,
        emergency_stop=False,
        provider="gemini",
        allowed_models_json=["gemini-test"],
    )


def test_planner_rebinds_gemini_source_to_authoritative_snapshot_before_hash():
    book = snapshot(requested_range="'Daily'!A1:Z80")
    proposal = make_plan(book, [op(cell="D2")])
    proposal.source = PlanSource(
        spreadsheet_file_id="gemini-selected-workbook",
        source_hash="0" * 64,
        sheet="Gemini Selected Sheet",
        range="A1:H22",
    )
    proposal_hash = plan_hash(proposal)
    planner, _gateway = planner_for(
        _enabled_control(), proposal.model_dump(mode="json")
    )

    final_plan, _model = planner.plan(
        tenant_id="tenant-a",
        business_date="2026-08-24",
        snapshot=book,
        business_goal=["safe rollover"],
    )

    assert final_plan.source == PlanSource(
        spreadsheet_file_id=book.spreadsheet_file_id,
        source_hash=book.source_hash,
        sheet=book.sheet_title,
        range=book.requested_range,
    )
    assert final_plan.operations[0].cell == "D2"
    assert plan_hash(final_plan) != proposal_hash

    result = agent_service(
        "shadow", final_plan, book, FakeExecutor()
    ).plan_agent_run("tenant-a", date(2026, 8, 24), dry_run=True)
    assert result.plan_hash == plan_hash(final_plan)


def test_rebinding_does_not_make_out_of_range_operation_safe():
    book = snapshot(requested_range="'Daily'!A1:Z80")
    proposal = make_plan(book, [op(cell="AA81", copy_from=None)])
    rebound = bind_authoritative_source(proposal, book)

    result = SheetAgentSafetyGuard().validate(
        tenant_id="tenant-a",
        plan=rebound,
        snapshot=book,
        allowed_range="'Daily'!A1:Z80",
        max_operations=200,
        allow_structure_changes=False,
        allow_formula_changes=False,
    )

    assert "target_out_of_range:carry" in result.errors
    assert rebound.operations[0].cell == "AA81"


def test_cell_evidence_uses_exact_a1_addresses_and_omits_empty_trailing_cells():
    book = snapshot(
        formula="=H2",
        requested_range="'Daily'!A1:Z80",
    )

    cells = build_cell_evidence(book)
    by_address = {item["cell"]: item for item in cells}

    assert by_address["A1"] == {"cell": "A1", "value": "STT", "formula": None}
    assert by_address["D2"] == {"cell": "D2", "value": "", "formula": "=H2"}
    assert by_address["H2"]["value"] == "12"
    assert by_address["D3"] == {"cell": "D3", "value": "", "formula": None}
    assert len(cells) == 24
    assert all(not item["cell"].startswith("Z") for item in cells)


def test_planner_payload_uses_cell_evidence_instead_of_parallel_matrices():
    book = snapshot(formula="=H2", requested_range="'Daily'!A1:Z80")
    response = make_plan(book, []).model_dump(mode="json")
    planner, gateway = planner_for(_enabled_control(), response)

    planner.plan(
        tenant_id="tenant-a",
        business_date="2026-08-24",
        snapshot=book,
        business_goal=["safe rollover"],
    )

    payload = json.loads(gateway.calls[0]["prompt"].split("INPUT:\n", 1)[1])
    assert payload["snapshot"]["requested_range"] == "'Daily'!A1:Z80"
    assert not ({"raw_values", "formulas", "coordinates"} & payload["snapshot"].keys())
    assert {item["cell"] for item in payload["cell_evidence"]} >= {"A1", "D2", "H2"}


def test_planner_prompt_requires_grounded_a1_and_material_source_identity():
    assert "issue.cells" in PLANNER_INSTRUCTIONS
    assert "operation.cell" in PLANNER_INSTRUCTIONS
    assert "operation.evidence_cells" in PLANNER_INSTRUCTIONS
    assert "operation.copy_from" in PLANNER_INSTRUCTIONS
    assert "supplied cell_evidence" in PLANNER_INSTRUCTIONS
    assert "Never calculate an A1 address from an array position" in PLANNER_INSTRUCTIONS
    assert "exact workbook material/item identity value" in PLANNER_INSTRUCTIONS
    assert "do not substitute the canonical material name" in PLANNER_INSTRUCTIONS


def test_missing_closing_evidence_does_not_create_operations():
    book = snapshot(closing="", requested_range="'Daily'!A1:Z80")
    proposal = make_plan(book, [], status="review_required", requires_review=True)
    planner, _gateway = planner_for(
        _enabled_control(), proposal.model_dump(mode="json")
    )

    final_plan, _model = planner.plan(
        tenant_id="tenant-a",
        business_date="2026-08-24",
        snapshot=book,
        business_goal=["safe rollover"],
    )

    assert final_plan.operations == []
    assert final_plan.source.range == "'Daily'!A1:Z80"


def test_planner_policy_distinguishes_coherent_compound_split_and_blank():
    assert 'P12 = "1(238,5g); 2(299,4g)"' in PLANNER_INSTRUCTIONS
    assert "is not an issue merely because it is compound" in PLANNER_INSTRUCTIONS
    assert 'P12 = "1(350"' in PLANNER_INSTRUCTIONS
    assert 'Q12 = "5g)"' in PLANNER_INSTRUCTIONS
    assert "suspected adjacent-cell split" in PLANNER_INSTRUCTIONS
    assert "require review" in PLANNER_INSTRUCTIONS
    assert "Blank is not zero" in PLANNER_INSTRUCTIONS
    assert "R12 = blank is unknown/missing and is never zero" in PLANNER_INSTRUCTIONS
    assert "Complexity of formatting alone does not" in PLANNER_INSTRUCTIONS


def test_planner_policy_defines_status_and_internal_semantic_checklist():
    assert "Plan status semantics: ready" in PLANNER_INSTRUCTIONS
    assert "Informational issues with requires_review=false do not promote status" in PLANNER_INSTRUCTIONS
    assert "header semantics" in PLANNER_INSTRUCTIONS
    assert "item rows versus section/total rows" in PLANNER_INSTRUCTIONS
    assert "material/catalog resolution" in PLANNER_INSTRUCTIONS
    assert "do not reveal reasoning" in PLANNER_INSTRUCTIONS


def production_shaped_snapshot():
    raw = [
        ["Item key", "Section", "Material name", "Opening", "Used", "Inbound", "Fragment", "Closing"],
        ["M-001", "Primary", "Known material", "1(312g); 2(303,2g)", "", "", "", ""],
        ["", "TOTAL", "", "", "", "", "", ""],
        ["M-002", "Primary", "Unresolved material", "", "", "1(350", "5g)", ""],
        ["", "SECTION TOTAL", "", "", "", "", "", ""],
    ]
    return build_workbook_snapshot(
        spreadsheet_file_id="production-shaped-sheet",
        file_metadata={"name": "Inventory", "modifiedTime": "2026-08-24T00:00:00Z"},
        spreadsheet_metadata={
            "properties": {"title": "Inventory", "timeZone": "Asia/Ho_Chi_Minh"},
            "sheets": [{"properties": {"title": "Daily", "sheetId": 9}}],
        },
        requested_range="'Daily'!A1:Z80",
        raw_block={"values": raw},
        formula_block={"values": deepcopy(raw)},
    )


def test_production_shaped_semantic_acceptance_is_grounded_and_shadow_only():
    book = production_shaped_snapshot()
    proposal = EditPlan(
        status="review_required",
        requires_review=True,
        source=PlanSource(
            spreadsheet_file_id="gemini-cannot-select-source",
            source_hash="0" * 64,
            sheet="Other",
            range="A1:H5",
        ),
        operations=[],
        issues=[
            PlanIssue(
                type="suspected_split_cell",
                cells=["F4", "G4"],
                message="Adjacent fragments require review",
                requires_review=True,
            ),
            PlanIssue(
                type="missing_closing_quantity",
                cells=["H2", "H4"],
                message="Closing evidence is blank",
                requires_review=True,
            ),
        ],
        material_actions=[
            MaterialAction(
                action="NEW_MATERIAL",
                source_key="M-002",
                source_key_cell="A4",
                source_name="Unresolved material",
                source_name_cell="C4",
                requires_review=True,
            )
        ],
    )
    final_plan = bind_authoritative_source(proposal, book)
    guard = SheetAgentSafetyGuard().validate(
        tenant_id="tenant-a",
        plan=final_plan,
        snapshot=book,
        allowed_range="'Daily'!A1:Z80",
        max_operations=200,
        allow_structure_changes=False,
        allow_formula_changes=False,
    )
    assert final_plan.source.spreadsheet_file_id == book.spreadsheet_file_id
    assert final_plan.source.source_hash == book.source_hash
    assert final_plan.source.range == "'Daily'!A1:Z80"
    assert final_plan.operations == []
    assert final_plan.issues[0].cells == ["F4", "G4"]
    assert all("compound" not in issue.type for issue in final_plan.issues)
    assert guard.accepted and guard.requires_review

    executor = FakeExecutor()
    production_config = parse_daily_sheet_config({
        "version": 3,
        "mode": "gemini_sheet_agent",
        "source": {"sheet": "Daily", "range": "A1:Z80"},
        "agent": {"apply_mode": "shadow"},
    })
    service = InventoryDailySheetAgentService(
        planner=FakePlanner(final_plan),
        snapshot_loader=lambda tenant: book,
        executor_factory=lambda tenant, loader: executor,
        config_loader=lambda tenant: production_config,
        guard=SheetAgentSafetyGuard(),
    )
    result = service.plan_agent_run("tenant-a", date(2026, 8, 24), dry_run=True)
    assert result.status == "review_required"
    assert result.operation_count == 0
    assert executor.calls == []
