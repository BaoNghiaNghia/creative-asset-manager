from __future__ import annotations

from copy import deepcopy
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from app.modules.inventory.ai.gateway import (
    InventoryGeminiToolCall,
    InventoryGeminiToolTurn,
    RuntimeInventoryGeminiGateway,
)
from app.modules.inventory.daily_sheet.agent_v4.contracts import (
    CellEvidence,
    EvidenceReference,
    StagedEdits,
)
from app.modules.inventory.daily_sheet.agent_v4.service import (
    InventoryDailySheetV4Service,
)
from app.modules.inventory.daily_sheet.agent_v4.tools import (
    V4AgentLimitExceeded,
    V4AgentSafetyError,
    V4WorkbookToolHost,
    function_declarations,
)
from app.modules.inventory.daily_sheet.config import (
    GeminiToolSheetAgentConfig,
    parse_daily_sheet_config,
)


class FakeGoogle:
    def __init__(self):
        self.closed = False
        self.mutation_calls = []
        self.modified_time = "2030-08-09T00:00:00Z"
        self.values = {
            "'Arbitrary'!C7:D8": [["alpha", "10"], ["", "=D7"]],
            "'Arbitrary'!C7": [["alpha"]],
            "'Arbitrary'!D7": [["10"]],
            "'Arbitrary'!C8": [[""]],
            "'Arbitrary'!D8": [[""]],
        }
        self.formulas = {
            "'Arbitrary'!C7:D8": [["alpha", "10"], ["", "=D7"]],
            "'Arbitrary'!C7": [["alpha"]],
            "'Arbitrary'!D7": [["10"]],
            "'Arbitrary'!C8": [[""]],
            "'Arbitrary'!D8": [["=D7"]],
        }

    def close(self):
        self.closed = True

    def validate_native_spreadsheet(self, file_id):
        return {
            "id": file_id,
            "name": "Any workbook",
            "modifiedTime": self.modified_time,
        }

    def spreadsheet_metadata(self, _file_id):
        return {
            "properties": {"title": "Any workbook", "timeZone": "Asia/Ho_Chi_Minh"},
            "sheets": [
                {
                    "properties": {
                        "title": "Arbitrary",
                        "sheetId": 7,
                        "gridProperties": {"rowCount": 100, "columnCount": 20},
                    },
                    "merges": [
                        {
                            "sheetId": 7,
                            "startRowIndex": 8,
                            "endRowIndex": 9,
                            "startColumnIndex": 2,
                            "endColumnIndex": 4,
                        }
                    ],
                    "protectedRanges": [
                        {
                            "range": {
                                "sheetId": 7,
                                "startRowIndex": 9,
                                "endRowIndex": 10,
                                "startColumnIndex": 2,
                                "endColumnIndex": 3,
                            }
                        }
                    ],
                }
            ],
        }

    def batch_get_values(self, _file_id, ranges, *, value_render_option="UNFORMATTED_VALUE"):
        source = self.formulas if value_render_option == "FORMULA" else self.values
        return [{"range": value, "values": deepcopy(source.get(value, []))} for value in ranges]

    def batch_update_values(self, *_args, **_kwargs):
        self.mutation_calls.append("update")

    def batch_clear_values(self, *_args, **_kwargs):
        self.mutation_calls.append("clear")


class FakeSession:
    def __init__(self, control=None, material_ids=()):
        self.control = control
        self.material_ids = material_ids

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def scalar(self, _query):
        return self.control

    def scalars(self, _query):
        return list(self.material_ids)


class SessionFactory:
    def __init__(self, control=None, material_ids=()):
        self.control = control
        self.material_ids = material_ids

    def __call__(self):
        return FakeSession(self.control, self.material_ids)


def host(*, max_read_calls=20, max_read_cells=100, material_ids=()):
    return V4WorkbookToolHost(
        tenant_id="tenant-a",
        spreadsheet_file_id="sheet-1",
        allowed_sheets=["Arbitrary"],
        google=FakeGoogle(),
        session_factory=SessionFactory(material_ids=material_ids),
        max_read_calls=max_read_calls,
        max_read_cells=max_read_cells,
        max_edit_operations=20,
    )


def reference(evidence):
    return {
        "sheet": evidence["sheet"],
        "cell": evidence["cell"],
        "evidence_hash": evidence["evidence_hash"],
    }


def submit_grounded_assessment(
    tools,
    evidence,
    *,
    conclusion="Workbook evidence was inspected.",
    additional_reads_needed=False,
):
    return tools.submit_workbook_assessment(
        {
            "summary": "Grounded workbook assessment",
            "observations": [
                {
                    "code": "observed_workbook_evidence",
                    "conclusion": conclusion,
                    "evidence": [reference(evidence)],
                    "confidence": 1.0,
                }
            ],
            "uncertainties": [],
            "additional_reads_needed": additional_reads_needed,
        }
    )


def test_v4_config_defaults_shadow_and_has_no_fixed_range():
    config = parse_daily_sheet_config(
        {
            "version": 4,
            "mode": "gemini_tool_sheet_agent",
            "source": {"allowed_sheets": ["Arbitrary"]},
        }
    )
    assert isinstance(config, GeminiToolSheetAgentConfig)
    assert config.agent.apply_mode == "shadow"
    assert config.agent.max_tool_rounds == 8
    assert not hasattr(config.source, "range")


@pytest.mark.parametrize("apply_mode", ["shadow", "review", "auto"])
def test_v4_config_accepts_explicit_guarded_apply_modes(apply_mode):
    assert parse_daily_sheet_config(
        {
            "version": 4,
            "mode": "gemini_tool_sheet_agent",
            "agent": {"apply_mode": apply_mode},
        }
    ).agent.apply_mode == apply_mode


def test_v4_config_rejects_unknown_apply_mode():
    with pytest.raises(Exception):
        parse_daily_sheet_config(
            {
                "version": 4,
                "mode": "gemini_tool_sheet_agent",
                "agent": {"apply_mode": "enabled"},
            }
        )


def test_tool_gateway_uses_native_function_declarations(monkeypatch):
    captured = {}
    resolver = Mock()
    resolver.resolve.return_value = "secret"
    response = Mock(status_code=200)
    response.json.return_value = {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [
                        {
                            "functionCall": {
                                "name": "get_workbook_metadata",
                                "args": {},
                            }
                        }
                    ],
                }
            }
        ]
    }

    def post(_url, **kwargs):
        captured.update(kwargs)
        return response

    monkeypatch.setattr("app.modules.inventory.ai.gateway.httpx.post", post)
    gateway = RuntimeInventoryGeminiGateway(resolver)
    turn = gateway.generate_tool_turn(
        tenant_id="tenant-a",
        contents=[{"role": "user", "parts": [{"text": "goal"}]}],
        function_declarations=[{"name": "get_workbook_metadata", "parameters": {"type": "object"}}],
        provider="gemini",
        model="gemini-test",
    )
    assert turn.calls == (InventoryGeminiToolCall(name="get_workbook_metadata", arguments={}),)
    assert captured["json"]["tools"][0]["functionDeclarations"][0]["name"] == "get_workbook_metadata"
    assert "responseSchema" not in captured["json"]
    assert "responseJsonSchema" not in captured["json"]
    assert resolver.resolve.call_args.args == ("tenant-a",)


def test_metadata_is_authorized_and_contains_grid_merge_protection():
    result = host().get_workbook_metadata()
    assert result["spreadsheet_file_id"] == "sheet-1"
    assert result["timezone"] == "Asia/Ho_Chi_Minh"
    assert result["sheets"][0]["grid"] == {"rows": 100, "columns": 20}
    assert result["sheets"][0]["merged_ranges"]
    assert result["sheets"][0]["protected_ranges"]


def test_arbitrary_range_returns_cell_addressed_values_formulas_and_hashes():
    result = host().read_range(
        {"sheet": "Arbitrary", "a1_range": "C7:D8", "include_formulas": True}
    )
    assert [item["cell"] for item in result["cells"]] == ["C7", "D7", "C8", "D8"]
    assert result["cells"][0]["raw_value"] == "alpha"
    assert result["cells"][3]["formula"] == "=D7"
    assert all(len(item["evidence_hash"]) == 64 for item in result["cells"])


@pytest.mark.parametrize(
    "arguments",
    [
        {"sheet": "Other", "a1_range": "A1"},
        {"sheet": "Arbitrary", "a1_range": "Other!A1"},
        {"sheet": "Arbitrary", "a1_range": "D8:C7"},
    ],
)
def test_read_range_fails_closed_for_unauthorized_or_invalid_ranges(arguments):
    with pytest.raises(V4AgentSafetyError):
        host().read_range(arguments)


def test_read_limits_fail_before_unbounded_provider_read():
    tools = host(max_read_cells=3)
    with pytest.raises(V4AgentLimitExceeded):
        tools.read_range({"sheet": "Arbitrary", "a1_range": "C7:D8"})


def test_exact_copy_stages_sets_before_clears_and_never_mutates_google():
    tools = host()
    cells = tools.read_range({"sheet": "Arbitrary", "a1_range": "C7:D8"})["cells"]
    c7, d7, c8 = cells[0], cells[1], cells[2]
    submit_grounded_assessment(tools, c7)
    result = tools.stage_edits(
        {
            "status": "ready",
            "operations": [
                {
                    "operation_id": "clear",
                    "type": "clear_cell",
                    "sheet": "Arbitrary",
                    "cell": "C8",
                    "evidence": [reference(c8)],
                    "provenance": "transformed",
                    "requires_review": True,
                },
                {
                    "operation_id": "copy",
                    "type": "set_cell",
                    "sheet": "Arbitrary",
                    "cell": "C7",
                    "value": "10",
                    "evidence": [reference(c7), reference(d7)],
                    "provenance": "exact_copy",
                    "copy_from": reference(d7),
                },
            ],
        }
    )
    assert result["writes"] == 0
    assert [item.type for item in tools.staged.operations] == ["set_cell", "clear_cell"]
    assert tools.google.mutation_calls == []


def test_transformed_write_requires_review_and_blank_is_not_zero():
    tools = host()
    cells = tools.read_range({"sheet": "Arbitrary", "a1_range": "C7:C8"})["cells"]
    submit_grounded_assessment(tools, cells[1])
    with pytest.raises(V4AgentSafetyError):
        tools.stage_edits(
            {
                "status": "ready",
                "operations": [
                    {
                        "operation_id": "invent-zero",
                        "type": "set_cell",
                        "sheet": "Arbitrary",
                        "cell": "C8",
                        "value": "0",
                        "evidence": [reference(cells[1])],
                        "provenance": "exact_copy",
                        "copy_from": reference(cells[1]),
                    }
                ],
            }
        )


def test_transformed_write_is_review_required():
    tools = host()
    cell = tools.read_range({"sheet": "Arbitrary", "a1_range": "C7"})["cells"][0]
    submit_grounded_assessment(tools, cell)
    tools.stage_edits(
        {
            "status": "ready",
            "operations": [
                {
                    "operation_id": "transform",
                    "type": "set_cell",
                    "sheet": "Arbitrary",
                    "cell": "C7",
                    "value": "changed",
                    "evidence": [reference(cell)],
                    "provenance": "transformed",
                }
            ],
        }
    )
    assert tools.staged.status == "review_required"
    assert tools.staged.requires_review is True


@pytest.mark.parametrize("target", ["D8", "C9", "C10"])
def test_formula_merged_and_protected_targets_are_blocked(target):
    tools = host()
    evidence = tools.read_range({"sheet": "Arbitrary", "a1_range": target})["cells"][0]
    submit_grounded_assessment(tools, evidence)
    with pytest.raises(V4AgentSafetyError, match="formula_or_restricted_target"):
        tools.stage_edits(
            {
                "status": "ready",
                "operations": [
                    {
                        "operation_id": "blocked",
                        "type": "clear_cell",
                        "sheet": "Arbitrary",
                        "cell": target,
                        "evidence": [reference(evidence)],
                        "provenance": "transformed",
                    }
                ],
            }
        )


def test_duplicate_targets_and_missing_evidence_are_blocked():
    tools = host()
    evidence = tools.read_range({"sheet": "Arbitrary", "a1_range": "C7"})["cells"][0]
    submit_grounded_assessment(tools, evidence)
    operation = {
        "operation_id": "one",
        "type": "set_cell",
        "sheet": "Arbitrary",
        "cell": "C7",
        "value": "alpha",
        "evidence": [reference(evidence)],
        "provenance": "exact_copy",
        "copy_from": reference(evidence),
    }
    with pytest.raises(V4AgentSafetyError, match="duplicate_or_conflicting_target"):
        tools.stage_edits(
            {"status": "ready", "operations": [operation, {**operation, "operation_id": "two"}]}
        )


def test_stale_evidence_is_re_read_and_blocked():
    tools = host()
    evidence = tools.read_range({"sheet": "Arbitrary", "a1_range": "C7"})["cells"][0]
    submit_grounded_assessment(tools, evidence)
    tools.google.values["'Arbitrary'!C7"] = [["changed externally"]]
    with pytest.raises(V4AgentSafetyError, match="stale_evidence"):
        tools.stage_edits(
            {
                "status": "ready",
                "operations": [
                    {
                        "operation_id": "copy",
                        "type": "set_cell",
                        "sheet": "Arbitrary",
                        "cell": "C7",
                        "value": "alpha",
                        "evidence": [reference(evidence)],
                        "provenance": "exact_copy",
                        "copy_from": reference(evidence),
                    }
                ],
            }
        )


def test_non_match_material_actions_always_require_review():
    tools = host()
    evidence = tools.read_range({"sheet": "Arbitrary", "a1_range": "C7"})["cells"][0]
    submit_grounded_assessment(tools, evidence)
    tools.stage_edits(
        {
            "status": "ready",
            "material_actions": [
                {
                    "action": "NEW_MATERIAL",
                    "source_evidence": [reference(evidence)],
                    "reason": "No catalog match",
                }
            ],
        }
    )
    assert tools.staged.status == "review_required"
    assert tools.staged.requires_review


def test_match_existing_rejects_material_outside_tenant():
    tools = host(material_ids=())
    evidence = tools.read_range({"sheet": "Arbitrary", "a1_range": "C7"})["cells"][0]
    submit_grounded_assessment(tools, evidence)
    with pytest.raises(V4AgentSafetyError, match="invalid_tenant_material_match"):
        tools.stage_edits(
            {
                "status": "ready",
                "material_actions": [
                    {
                        "action": "MATCH_EXISTING",
                        "material_id": "tenant-b-item",
                        "source_evidence": [reference(evidence)],
                    }
                ],
            }
        )


class ScriptedGateway:
    def __init__(self):
        self.calls = 0
        self.requests = []

    @staticmethod
    def _turn(name, arguments):
        return InventoryGeminiToolTurn(
            content={
                "role": "model",
                "parts": [{"functionCall": {"name": name, "args": arguments}}],
            },
            calls=(InventoryGeminiToolCall(name, arguments),),
        )

    def generate_tool_turn(self, **kwargs):
        self.calls += 1
        self.requests.append(kwargs)
        if self.calls == 1:
            return self._turn("get_workbook_metadata", {})
        if self.calls == 2:
            return self._turn(
                "read_range",
                {"sheet": "Arbitrary", "a1_range": "C7", "include_formulas": True},
            )
        cells = kwargs["contents"][-1]["parts"][0]["functionResponse"]["response"].get("cells")
        if self.calls == 3:
            return self._turn(
                "submit_workbook_assessment",
                {
                    "summary": "The requested evidence is complete.",
                    "observations": [
                        {
                            "code": "workbook_reviewed",
                            "conclusion": "No staged edit is needed for the observed cell.",
                            "evidence": [reference(cells[0])],
                            "confidence": 1.0,
                        }
                    ],
                    "uncertainties": [],
                    "additional_reads_needed": False,
                },
            )
        return self._turn(
            "stage_edits",
            {
                "status": "ready",
                "summary": "No changes required",
                "operations": [],
                "issues": [],
                "material_actions": [],
            },
        )


def test_real_v4_service_tool_loop_is_shadow_and_does_not_call_legacy_parsers():
    control = SimpleNamespace(
        enabled=True,
        emergency_stop=False,
        provider="gemini",
        allowed_models_json=["gemini-test"],
    )
    config = parse_daily_sheet_config(
        {
            "version": 4,
            "mode": "gemini_tool_sheet_agent",
            "source": {"allowed_sheets": ["Arbitrary"]},
        }
    )
    google = FakeGoogle()
    service = InventoryDailySheetV4Service(
        session_factory=SessionFactory(control=control),
        gateway=ScriptedGateway(),
        context_provider=lambda _tenant: SimpleNamespace(
            config=config, working_file_id="sheet-1", connection_id="connection-1"
        ),
        client_factory=lambda _token: google,
        token_resolver=lambda _connection: "oauth-token",
        enabled=True,
    )
    with patch(
        "app.modules.inventory.daily_sheet.parser.parse_daily_count_records",
        side_effect=AssertionError("legacy parser called"),
    ), patch(
        "app.modules.inventory.materials.MaterialRegistry.resolve",
        side_effect=AssertionError("material resolver called"),
    ):
        result = service.run_shadow("tenant-a", date(2030, 8, 9))
    assert result.status == "shadow"
    assert result.apply_mode == "shadow"
    assert result.run_id is not None
    assert len(result.run_id) == 64
    assert result.writes == 0
    assert google.mutation_calls == []
    assert google.closed is True


def test_stage_tool_schema_is_authoritative_and_contains_operations():
    declaration = next(item for item in function_declarations() if item["name"] == "stage_edits")
    schema = declaration["parametersJsonSchema"]
    assert "operations" in schema["properties"]
    assert "material_actions" in schema["properties"]


def test_read_call_limit_is_bounded():
    tools = host(max_read_calls=1)
    tools.read_range({"sheet": "Arbitrary", "a1_range": "C7"})
    with pytest.raises(V4AgentLimitExceeded):
        tools.read_range({"sheet": "Arbitrary", "a1_range": "D7"})


def test_configured_spreadsheet_identity_mismatch_fails_before_gemini():
    control = SimpleNamespace(
        enabled=True,
        emergency_stop=False,
        provider="gemini",
        allowed_models_json=["gemini-test"],
    )
    config = parse_daily_sheet_config(
        {
            "version": 4,
            "mode": "gemini_tool_sheet_agent",
            "source": {"spreadsheet_file_id": "other-sheet"},
        }
    )
    gateway = ScriptedGateway()
    service = InventoryDailySheetV4Service(
        session_factory=SessionFactory(control=control),
        gateway=gateway,
        context_provider=lambda _tenant: SimpleNamespace(
            config=config, working_file_id="sheet-1", connection_id="connection-1"
        ),
        client_factory=lambda _token: FakeGoogle(),
        token_resolver=lambda _connection: "oauth-token",
        enabled=True,
    )
    with pytest.raises(V4AgentSafetyError, match="spreadsheet_not_authorized"):
        service.run_shadow("tenant-a", date(2030, 8, 9))
    assert gateway.calls == 0


def test_round_limit_fails_closed_and_closes_google():
    control = SimpleNamespace(
        enabled=True,
        emergency_stop=False,
        provider="gemini",
        allowed_models_json=["gemini-test"],
    )
    config = parse_daily_sheet_config(
        {
            "version": 4,
            "mode": "gemini_tool_sheet_agent",
            "agent": {"max_tool_rounds": 1},
        }
    )
    google = FakeGoogle()
    gateway = ScriptedGateway()
    service = InventoryDailySheetV4Service(
        session_factory=SessionFactory(control=control),
        gateway=gateway,
        context_provider=lambda _tenant: SimpleNamespace(
            config=config, working_file_id="sheet-1", connection_id="connection-1"
        ),
        client_factory=lambda _token: google,
        token_resolver=lambda _connection: "oauth-token",
        enabled=True,
    )
    with pytest.raises(Exception, match="inventory_sheet_agent_v4_round_limit"):
        service.run_shadow("tenant-a", date(2030, 8, 9))
    assert google.closed is True
    assert google.mutation_calls == []


def test_evidence_ledger_records_exact_cells():
    tools = host()
    result = tools.read_range({"sheet": "Arbitrary", "a1_range": "C7:D7"})
    assert set(tools.ledger) == {("Arbitrary", "C7"), ("Arbitrary", "D7")}
    assert tools.ledger[("Arbitrary", "D7")].evidence_hash == result["cells"][1]["evidence_hash"]


def test_out_of_grid_target_is_blocked_mechanically():
    tools = host()
    tools.ledger[("Arbitrary", "U101")] = CellEvidence(
        sheet="Arbitrary",
        cell="U101",
        raw_value=None,
        evidence_hash="a" * 64,
    )
    submit_grounded_assessment(
        tools,
        tools.ledger[("Arbitrary", "U101")].model_dump(mode="json"),
    )
    with pytest.raises(V4AgentSafetyError, match="target_out_of_grid"):
        tools.stage_edits(
            {
                "status": "ready",
                "operations": [
                    {
                        "operation_id": "outside",
                        "type": "clear_cell",
                        "sheet": "Arbitrary",
                        "cell": "U101",
                        "evidence": [
                            {"sheet": "Arbitrary", "cell": "U101", "evidence_hash": "a" * 64}
                        ],
                        "provenance": "transformed",
                    }
                ],
            }
        )


def test_edit_operation_limit_is_enforced():
    tools = host()
    tools.max_edit_operations = 1
    evidence = tools.read_range({"sheet": "Arbitrary", "a1_range": "C7:D7"})["cells"]
    submit_grounded_assessment(tools, evidence[0])
    operations = []
    for index, item in enumerate(evidence):
        operations.append(
            {
                "operation_id": str(index),
                "type": "clear_cell",
                "sheet": "Arbitrary",
                "cell": item["cell"],
                "evidence": [reference(item)],
                "provenance": "transformed",
            }
        )
    with pytest.raises(V4AgentLimitExceeded, match="edit_operation_limit_exceeded"):
        tools.stage_edits({"status": "review_required", "operations": operations})


@pytest.mark.parametrize("action", ["NEW_MATERIAL", "POSSIBLE_RENAME", "AMBIGUOUS"])
def test_non_matching_material_decisions_require_review(action):
    tools = host()
    evidence = tools.read_range({"sheet": "Arbitrary", "a1_range": "C7"})["cells"][0]
    submit_grounded_assessment(tools, evidence)
    tools.stage_edits(
        {
            "status": "ready",
            "material_actions": [
                {"action": action, "source_evidence": [reference(evidence)]}
            ],
        }
    )
    assert tools.staged.status == "review_required"
    assert tools.staged.requires_review is True


def test_gemini_tool_payload_never_contains_credentials(monkeypatch):
    captured = {}
    resolver = Mock()
    resolver.resolve.return_value = "gemini-secret-marker"
    response = Mock(status_code=200)
    response.json.return_value = {
        "candidates": [{"content": {"role": "model", "parts": [{"functionCall": {"name": "get_workbook_metadata", "args": {}}}]}}]
    }
    monkeypatch.setattr(
        "app.modules.inventory.ai.gateway.httpx.post",
        lambda _url, **kwargs: captured.update(kwargs) or response,
    )
    RuntimeInventoryGeminiGateway(resolver).generate_tool_turn(
        tenant_id="tenant-a",
        contents=[{"role": "user", "parts": [{"text": "goal"}]}],
        function_declarations=function_declarations(),
        provider="gemini",
        model="gemini-test",
    )
    serialized = json.dumps(captured["json"], sort_keys=True)
    assert "gemini-secret-marker" not in serialized
    assert "oauth-token" not in serialized


def test_v41_business_goal_is_configurable_and_has_safe_defaults():
    default = parse_daily_sheet_config(
        {
            "version": 4,
            "mode": "gemini_tool_sheet_agent",
            "source": {"allowed_sheets": ["Arbitrary"]},
        }
    )
    assert default.agent.business_goal
    custom = parse_daily_sheet_config(
        {
            "version": 4,
            "mode": "gemini_tool_sheet_agent",
            "agent": {"business_goal": ["Investigate this workbook on its own terms."]},
        }
    )
    assert custom.agent.business_goal == [
        "Investigate this workbook on its own terms."
    ]


def test_v41_tool_contract_exposes_native_assessment_before_stage():
    declarations = function_declarations()
    names = [item["name"] for item in declarations]
    assert names.index("submit_workbook_assessment") < names.index("stage_edits")
    assessment = next(
        item for item in declarations if item["name"] == "submit_workbook_assessment"
    )
    schema = assessment["parametersJsonSchema"]
    assert {"summary", "observations", "uncertainties"} <= set(schema["properties"])


@pytest.mark.parametrize(
    "bad_reference",
    [
        {"sheet": "Arbitrary", "cell": "D7", "evidence_hash": "a" * 64},
        {"sheet": "Arbitrary", "cell": "C7", "evidence_hash": "b" * 64},
    ],
)
def test_v41_assessment_rejects_missing_or_hash_mismatched_evidence(bad_reference):
    tools = host()
    tools.read_range({"sheet": "Arbitrary", "a1_range": "C7"})
    with pytest.raises(V4AgentSafetyError, match="missing_or_invalid_evidence"):
        tools.submit_workbook_assessment(
            {
                "summary": "Invalid evidence must fail closed.",
                "observations": [
                    {
                        "code": "untrusted",
                        "conclusion": "This conclusion is not grounded.",
                        "evidence": [bad_reference],
                        "confidence": 0.5,
                    }
                ],
                "uncertainties": [],
                "additional_reads_needed": False,
            }
        )


def test_v41_stage_before_assessment_is_rejected():
    tools = host()
    tools.read_range({"sheet": "Arbitrary", "a1_range": "C7"})
    with pytest.raises(V4AgentSafetyError, match="assessment_required"):
        tools.stage_edits(
            {
                "status": "ready",
                "summary": "Premature no-op",
                "operations": [],
                "issues": [],
                "material_actions": [],
            }
        )


def test_v41_grounded_assessment_allows_ready_noop():
    tools = host()
    evidence = tools.read_range(
        {"sheet": "Arbitrary", "a1_range": "D7"}
    )["cells"][0]
    submit_grounded_assessment(
        tools,
        evidence,
        conclusion="Opaque business conclusion chosen entirely by the model.",
    )
    result = tools.stage_edits(
        {
            "status": "ready",
            "summary": "No shadow operations are appropriate.",
            "operations": [],
            "issues": [],
            "material_actions": [],
        }
    )
    assert result["accepted"] is True
    assert result["writes"] == 0
    assert tools.staged.status == "ready"


def test_v41_model_can_read_after_assessment_then_resubmit_complete_assessment():
    tools = host()
    first = tools.read_range(
        {"sheet": "Arbitrary", "a1_range": "C7"}
    )["cells"][0]
    submit_grounded_assessment(tools, first, additional_reads_needed=True)
    second = tools.read_range(
        {"sheet": "Arbitrary", "a1_range": "D7"}
    )["cells"][0]
    tools.submit_workbook_assessment(
        {
            "summary": "The follow-up evidence completes the assessment.",
            "observations": [
                {
                    "code": "adaptive_investigation",
                    "conclusion": "The model selected a second arbitrary cell.",
                    "evidence": [reference(first), reference(second)],
                    "confidence": 0.9,
                }
            ],
            "uncertainties": [],
            "additional_reads_needed": False,
        }
    )
    result = tools.stage_edits(
        {
            "status": "ready",
            "summary": "Adaptive investigation is complete.",
            "operations": [],
            "issues": [],
            "material_actions": [],
        }
    )
    assert result["accepted"] is True
    assert tools.assessment.additional_reads_needed is False


def test_v41_material_catalog_is_only_read_when_model_requests_it():
    tools = host()
    read = tools.execute(
        "read_range",
        {"sheet": "Arbitrary", "a1_range": "C7", "include_formulas": True},
    )
    tools.execute(
        "submit_workbook_assessment",
        {
            "summary": "Material identity is not relevant.",
            "observations": [
                {
                    "code": "catalog_not_needed",
                    "conclusion": "The read evidence needs no material lookup.",
                    "evidence": [reference(read["cells"][0])],
                    "confidence": 1.0,
                }
            ],
            "uncertainties": [],
            "additional_reads_needed": False,
        },
    )
    tools.execute(
        "stage_edits",
        {
            "status": "ready",
            "summary": "No changes required.",
            "operations": [],
            "issues": [],
            "material_actions": [],
        },
    )
    assert "get_material_catalog" not in [item["tool"] for item in tools.tool_trace]


def test_v41_tool_trace_contains_only_safe_structural_diagnostics():
    tools = host()
    read = tools.execute(
        "read_range",
        {"sheet": "Arbitrary", "a1_range": "C7:D7", "include_formulas": True},
    )
    tools.execute(
        "submit_workbook_assessment",
        {
            "summary": "Assessment text must not be copied into diagnostics.",
            "observations": [
                {
                    "code": "safe_trace",
                    "conclusion": "Sensitive workbook values stay out of trace.",
                    "evidence": [reference(read["cells"][0])],
                    "confidence": 1.0,
                }
            ],
            "uncertainties": [],
            "additional_reads_needed": False,
        },
    )
    serialized = json.dumps(tools.tool_trace, sort_keys=True)
    assert "alpha" not in serialized
    assert "secret" not in serialized
    assert tools.tool_trace == [
        {
            "tool": "read_range",
            "sheet": "Arbitrary",
            "range": "'Arbitrary'!C7:D7",
            "cells": 2,
        },
        {
            "tool": "submit_workbook_assessment",
            "observations": 1,
            "uncertainties": 0,
            "additional_reads_needed": False,
        },
    ]


class ProductionShapedGoogle(FakeGoogle):
    def __init__(self):
        super().__init__()
        values = [
            ["Item", "Opening", "Incoming", "Usage part 1", "Usage part 2", "Final"],
            ["Known material", "10", "2", "3", "", "9"],
            ["Split material", "8", "1", "2", "2", "5"],
            ["Unlisted material", "4", "", "1", "", ""],
            ["Notes", "", "", "", "", ""],
        ]
        self.values["'Arbitrary'!A1:F5"] = deepcopy(values)
        self.formulas["'Arbitrary'!A1:F5"] = deepcopy(values)
        for row_index, row in enumerate(values, start=1):
            for column_index, value in enumerate(row, start=1):
                column = chr(64 + column_index)
                qualified = f"'Arbitrary'!{column}{row_index}"
                self.values[qualified] = [[value]]
                self.formulas[qualified] = [[value]]


class ProductionShapedGateway(ScriptedGateway):
    def __init__(self):
        super().__init__()
        self.cells = {}

    def generate_tool_turn(self, **kwargs):
        self.calls += 1
        self.requests.append(kwargs)
        if self.calls == 1:
            return self._turn("get_workbook_metadata", {})
        if self.calls == 2:
            return self._turn(
                "read_range",
                {
                    "sheet": "Arbitrary",
                    "a1_range": "A1:F5",
                    "include_formulas": True,
                },
            )
        if self.calls == 3:
            response = kwargs["contents"][-1]["parts"][0]["functionResponse"]["response"]
            self.cells = {item["cell"]: item for item in response["cells"]}
            return self._turn("get_material_catalog", {})
        if self.calls == 4:
            return self._turn(
                "submit_workbook_assessment",
                {
                    "summary": "The workbook contains a split field, an unresolved material, and missing final evidence.",
                    "observations": [
                        {
                            "code": "split_field_observed",
                            "conclusion": "Two adjacent cells participate in one row-local usage relationship.",
                            "evidence": [
                                reference(self.cells["D3"]),
                                reference(self.cells["E3"]),
                            ],
                            "confidence": 0.98,
                        },
                        {
                            "code": "material_requires_review",
                            "conclusion": "The row label does not identify an approved catalog item.",
                            "evidence": [reference(self.cells["A4"])],
                            "confidence": 0.95,
                        },
                    ],
                    "uncertainties": [
                        {
                            "code": "missing_final_value",
                            "message": "The final value is blank and cannot be invented.",
                            "evidence": [reference(self.cells["F4"])],
                        }
                    ],
                    "additional_reads_needed": False,
                },
            )
        return self._turn(
            "stage_edits",
            {
                "status": "review_required",
                "summary": "Review is required; no Google write is staged.",
                "operations": [],
                "issues": [
                    {
                        "code": "split_field_requires_review",
                        "message": "The row contains split usage evidence.",
                        "evidence": [
                            reference(self.cells["D3"]),
                            reference(self.cells["E3"]),
                        ],
                        "requires_review": True,
                    },
                    {
                        "code": "missing_final_value",
                        "message": "Final evidence is absent.",
                        "evidence": [reference(self.cells["F4"])],
                        "requires_review": True,
                    },
                ],
                "material_actions": [
                    {
                        "action": "NEW_MATERIAL",
                        "source_evidence": [reference(self.cells["A4"])],
                        "reason": "No approved catalog identity was established.",
                    }
                ],
            },
        )


class PrematureStageGateway(ScriptedGateway):
    def generate_tool_turn(self, **kwargs):
        self.calls += 1
        self.requests.append(kwargs)
        if self.calls == 1:
            return self._turn("get_workbook_metadata", {})
        if self.calls == 2:
            return self._turn(
                "read_range",
                {"sheet": "Arbitrary", "a1_range": "C7", "include_formulas": True},
            )
        return self._turn(
            "stage_edits",
            {
                "status": "ready",
                "summary": "Premature and ungrounded.",
                "operations": [],
                "issues": [],
                "material_actions": [],
            },
        )


def v41_service(*, gateway, google, business_goal=None):
    control = SimpleNamespace(
        enabled=True,
        emergency_stop=False,
        provider="gemini",
        allowed_models_json=["gemini-test"],
    )
    agent = {}
    if business_goal is not None:
        agent["business_goal"] = business_goal
    config = parse_daily_sheet_config(
        {
            "version": 4,
            "mode": "gemini_tool_sheet_agent",
            "source": {"allowed_sheets": ["Arbitrary"]},
            "agent": agent,
        }
    )
    material = SimpleNamespace(
        id="material-known",
        sku="KNOWN",
        name="Known material",
        category="test",
        base_unit="unit",
        preferred_unit="unit",
    )
    return InventoryDailySheetV4Service(
        session_factory=SessionFactory(control=control, material_ids=[material]),
        gateway=gateway,
        context_provider=lambda _tenant: SimpleNamespace(
            config=config,
            working_file_id="sheet-1",
            connection_id="connection-1",
        ),
        client_factory=lambda _token: google,
        token_resolver=lambda _connection: "oauth-token",
        enabled=True,
    )


def test_v41_production_shaped_semantic_sequence_is_grounded_shadow_review():
    google = ProductionShapedGoogle()
    gateway = ProductionShapedGateway()
    result = v41_service(
        gateway=gateway,
        google=google,
        business_goal=["Investigate quantities, materials, and missing evidence."],
    ).run_shadow("tenant-a", date(2030, 8, 9))
    assert result.status == "review_required"
    assert result.writes == 0
    assert result.assessment_present is True
    assert result.catalog_read is True
    assert result.tools_called == [
        "get_workbook_metadata",
        "read_range",
        "get_material_catalog",
        "submit_workbook_assessment",
        "stage_edits",
    ]
    assert result.ranges_read == ["'Arbitrary'!A1:F5"]
    assert result.staged.operations == []
    assert {issue.code for issue in result.staged.issues} == {
        "split_field_requires_review",
        "missing_final_value",
    }
    assert [action.action for action in result.staged.material_actions] == [
        "NEW_MATERIAL"
    ]
    assert google.mutation_calls == []
    assert google.closed is True
    prompt = gateway.requests[0]["contents"][0]["parts"][0]["text"]
    assert "Investigate quantities, materials, and missing evidence." in prompt


def test_v41_bad_sequence_stage_without_assessment_fails_closed():
    google = FakeGoogle()
    gateway = PrematureStageGateway()
    with pytest.raises(V4AgentSafetyError, match="assessment_required"):
        v41_service(gateway=gateway, google=google).run_shadow(
            "tenant-a", date(2030, 8, 9)
        )
    assert google.mutation_calls == []
    assert google.closed is True



def test_v4_live_executor_revalidates_then_sets_verifies_and_clears():
    google = FakeGoogle()

    def update(_sheet, updates):
        google.mutation_calls.append("update")
        for item in updates:
            google.values[item["range"]] = deepcopy(item["values"])
            google.formulas[item["range"]] = deepcopy(item["values"])

    def clear(_sheet, ranges):
        google.mutation_calls.append("clear")
        for item in ranges:
            google.values[item] = []
            google.formulas[item] = []

    google.batch_update_values = update
    google.batch_clear_values = clear
    tools = V4WorkbookToolHost(
        tenant_id="tenant-a",
        spreadsheet_file_id="sheet-1",
        allowed_sheets=["Arbitrary"],
        google=google,
        session_factory=SessionFactory(),
        max_read_calls=20,
        max_read_cells=100,
        max_edit_operations=20,
    )
    c7, d7, c8 = tools.read_range(
        {"sheet": "Arbitrary", "a1_range": "C7:D8"}
    )["cells"][:3]
    submit_grounded_assessment(tools, c7)
    tools.stage_edits(
        {
            "status": "ready",
            "operations": [
                {
                    "operation_id": "copy",
                    "type": "set_cell",
                    "sheet": "Arbitrary",
                    "cell": "C8",
                    "value": "alpha",
                    "evidence": [reference(c8), reference(c7)],
                    "provenance": "exact_copy",
                    "copy_from": reference(c7),
                },
                {
                    "operation_id": "clear",
                    "type": "clear_cell",
                    "sheet": "Arbitrary",
                    "cell": "D7",
                    "evidence": [reference(d7)],
                    "provenance": "exact_copy",
                    "copy_from": reference(d7),
                },
            ],
        }
    )

    result = tools.apply_staged()

    assert result == {
        "status": "completed",
        "writes": 2,
        "set_count": 1,
        "clear_count": 1,
        "verification_status": "verified",
    }
    assert google.mutation_calls == ["update", "clear"]
