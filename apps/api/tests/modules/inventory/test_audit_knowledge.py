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


def test_gemini_learning_context_includes_verified_patterns_and_conflict_warnings_only():
    temp, engine, sessions = db()
    from app.modules.inventory.persistence_model import InventoryOperationAuditModel
    knowledge = InventoryKnowledgeService(sessions)
    knowledge.create(
        "tenant-a", kind="BUSINESS_NOTE", title="Conflicting observations",
        content="Treat X as correct", scope_type="sheet", scope_key="Kho",
        status="proposed", source="verified_daily_observation",
        structured_rule={"learner": "verified_daily_assessment_v2",
                         "consistent_conclusion": False, "distinct_conclusions": 3},
    )
    knowledge.create(
        "tenant-a", kind="BUSINESS_NOTE", title="Consistent observed pattern",
        content="Look for a duplicate intake row", scope_type="sheet", scope_key="Kho",
        status="proposed", source="verified_daily_observation",
        structured_rule={"learner": "verified_daily_assessment_v2",
                         "consistent_conclusion": True, "support": 3},
    )
    with sessions.begin() as session:
        session.add(InventoryOperationAuditModel(
            tenant_id="tenant-a", business_date=date(2026, 10, 11),
            stage="evening_reconcile", status="completed",
            summary_json={"read_cells": 420, "verification_status": "verified"},
            assessment_json={"observations": [{
                "code": "ROW_VALUE_MISMATCH",
                "conclusion": "Closing balance differs from source reference",
                "evidence": [{"sheet": "Kho", "cell": "D7", "evidence_hash": "a"*64}],
                "confidence": 0.99,
            }, {
                "code": "METADATA_AND_STRUCTURE_VERIFIED",
                "conclusion": "Only inspected headers",
                "evidence": [{"sheet": "Kho", "cell": "A1", "evidence_hash": "b"*64}],
                "confidence": 0.95,
            }]},
        ))
    memory = knowledge.gemini_learning_context("tenant-a", sheet_keys=("Kho",))
    assert memory["kind"] == "historical_advisory_not_authority"
    assert memory["conflicting_topics"] == ["Conflicting observations"]
    assert len(memory["consistent_patterns"]) == 1
    assert len(memory["verified_observations"]) == 1
    assert memory["verified_observations"][0]["code"] == "ROW_VALUE_MISMATCH"
    assert memory["verified_observations"][0]["business_date"] == "2026-10-11"
    assert memory["verified_observations"][0]["sheet"] == "Kho"
    assert memory["verified_observations"][0]["cell"] == "D7"
    assert "Treat X as correct" not in str(memory)  # conflicting conclusion never taught as rule
    assert len(memory["hash"]) == 64
    assert memory["approved_rules_only"] is False
    assert knowledge.gemini_learning_context("tenant-a", sheet_keys=("Elsewhere",))["verified_observations"] == []
    engine.dispose();temp.cleanup()


def test_conflicting_verified_observation_cannot_be_activated_until_revised():
    temp, engine, sessions = db()
    knowledge = InventoryKnowledgeService(sessions)
    candidate = knowledge.create(
        "tenant-a", kind="BUSINESS_NOTE", title="Conflicting column",
        content="Verified observations disagree across days.",
        scope_type="sheet", scope_key="Kho", status="proposed",
        source="verified_daily_observation",
        structured_rule={
            "learner": "verified_daily_assessment_v2",
            "consistent_conclusion": False,
            "distinct_conclusions": 3,
            "requires_human_review": True,
            "executable": False,
        },
    )
    with pytest.raises(
        InventoryKnowledgeError, match="inventory_knowledge_conflicting_observations"
    ):
        knowledge.activate("tenant-a", candidate["id"], "user-a")
    assert knowledge.list("tenant-a")[0]["status"] == "proposed"
    revision = knowledge.revise(
        "tenant-a", candidate["id"], actor_id="user-a",
        content="Reviewed: column B holds the warehouse code.",
        structured_rule={"reviewed_evidence": True, "executable": False},
    )
    assert knowledge.activate("tenant-a", revision["id"], "user-a")["status"] == "active"
    engine.dispose(); temp.cleanup()


def test_learning_health_reports_conflicts_and_stale_verified_days():
    temp, engine, sessions = db()
    service = InventoryKnowledgeService(sessions)
    proposal = service.create(
        "tenant-a", kind="BUSINESS_NOTE", title="Contradictory assessment",
        content="Requires evidence review", status="proposed",
        source="verified_daily_observation",
        structured_rule={
            "learner": "verified_daily_assessment_v2",
            "consistent_conclusion": False,
            "distinct_conclusions": 2,
        },
    )
    from app.modules.inventory.persistence_model import InventoryOperationAuditModel
    with sessions.begin() as session:
        session.add(InventoryOperationAuditModel(
            tenant_id="tenant-a", business_date=date(2026, 10, 9),
            stage="evening_reconcile", status="completed",
            summary_json={"read_cells": 42, "verification_status": "verified",
                          "learning": {"status": "failed", "assessment_error": "TimeoutError"}},
        ))
    health = service.learning_status("tenant-a", today=date(2026, 10, 12))["health"]
    assert health["status"] == "critical"
    assert health["conflicting_proposals"] == 1
    assert "learning_failed" in health["issue_codes"]
    assert "verified_data_stale" in health["issue_codes"]
    assert health["last_verified_age_days"] == 3
    assert service.list("tenant-a")[0]["id"] == proposal["id"]
    engine.dispose(); temp.cleanup()


def test_learning_health_waits_for_first_post_deploy_run_without_false_staleness():
    temp, engine, sessions = db()
    service = InventoryKnowledgeService(sessions)
    from app.modules.inventory.persistence_model import InventoryOperationAuditModel
    with sessions.begin() as session:
        session.add(InventoryOperationAuditModel(
            tenant_id="tenant-a", business_date=date(2026, 10, 11),
            stage="evening_reconcile", status="completed",
            summary_json={"read_cells": 12, "verification_status": "verified"},
        ))
    health = service.learning_status("tenant-a", today=date(2026, 10, 12))["health"]
    assert health["status"] == "watching"
    assert health["issue_codes"] == []
    engine.dispose(); temp.cleanup()


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



def test_learning_failures_are_audited_without_reverting_verified_operations(monkeypatch, caplog):
    temp, engine, sessions = db()
    def offline(*_args, **_kwargs):
        raise RuntimeError("downstream learner unavailable")
    monkeypatch.setattr(InventoryKnowledgeService, "learn_from_verified_history", offline)
    monkeypatch.setattr(InventoryKnowledgeService, "learn_from_verified_assessments", offline)
    result = SimpleNamespace(
        run_id="c"*64, status="completed", tool_rounds=1, read_calls=1,
        read_cells=12, writes=0, plan_hash="p"*64,
        staged=SimpleNamespace(operations=[], issues=[], material_actions=[], summary="verified no-op"),
        assessment={"observations": []}, tool_trace=[], ranges_read=[],
        business_prompt_source="default", business_prompt_version="v1",
        business_prompt_hash="h"*64, knowledge_hash="k"*64, knowledge_version=0,
        model="gemini-test", execution={"verification_status": "verified"},
        knowledge_proposals=[], change_audit=[],
    )
    with caplog.at_level("WARNING", logger="cam.inventory.learning"):
        audit = InventoryOperationAuditService(sessions).persist_v4_result(
            "tenant-a", date(2026, 10, 11),
            stage="evening_reconcile", result=result,
        )
    assert audit["status"] == "completed"
    assert audit["summary"]["learning"]["status"] == "failed"
    assert audit["summary"]["learning"]["history_error"] == "RuntimeError"
    assert audit["summary"]["learning"]["assessment_error"] == "RuntimeError"
    assert sum("inventory_learning_failed" in entry.message for entry in caplog.records) == 2
    again = InventoryOperationAuditService(sessions).get_by_id("tenant-a", audit["id"])
    assert again["summary"]["learning"]["status"] == "failed"
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



def test_verified_daily_assessments_generate_reviewable_knowledge_without_cell_writes():
    temp, engine, sessions = db()
    audit_service = InventoryOperationAuditService(sessions)
    knowledge = InventoryKnowledgeService(sessions)

    def observation_result(run_id, business_date):
        return SimpleNamespace(
            run_id=run_id, status="completed", tool_rounds=2, read_calls=3,
            read_cells=24, writes=0, plan_hash="p"*64,
            staged=SimpleNamespace(operations=[], issues=[], material_actions=[], summary="no edits required"),
            assessment={
                "summary": "Workbook structure inspected",
                "observations": [{
                    "code": "WAREHOUSE_COLUMN_LABEL",
                    "conclusion": "Warehouse code is stored in column B",
                    "confidence": 0.94,
                    "evidence": [{
                        "sheet": "Kho", "cell": "B2",
                        "evidence_hash": "a"*64,
                    }],
                }],
                "uncertainties": [], "additional_reads_needed": False,
            },
            tool_trace=[], ranges_read=["'Kho'!A1:C8"],
            business_prompt_source="default", business_prompt_version="v1",
            business_prompt_hash="h"*64, knowledge_hash="k"*64,
            knowledge_version=0, model="gemini-test",
            execution={"verification_status": "verified"},
            knowledge_proposals=[], change_audit=[],
        )

    audit_service.persist_v4_result(
        "tenant-a", date(2026, 10, 10), stage="evening_reconcile",
        result=observation_result("a"*64, "2026-10-10"),
    )
    audit_service.persist_v4_result(
        "tenant-a", date(2026, 10, 11), stage="evening_reconcile",
        result=observation_result("b"*64, "2026-10-11"),
    )
    entries = [
        item for item in knowledge.list("tenant-a")
        if item["source"] == "verified_daily_observation"
    ]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["status"] == "proposed"
    assert entry["kind"] == "BUSINESS_NOTE"
    assert entry["structured_rule"]["distinct_business_dates"] == 2
    assert entry["structured_rule"]["executable"] is False
    assert entry["structured_rule"]["mode"] == "shadow"
    assert len(entry["evidence"]) == 2
    assert knowledge.active_snapshot("tenant-a")["count"] == 0

    # A scheduler replay must not duplicate a previously learned candidate.
    knowledge.learn_from_verified_assessments("tenant-a")
    assert len([
        item for item in knowledge.list("tenant-a")
        if item["source"] == "verified_daily_observation"
    ]) == 1

    engine.dispose(); temp.cleanup()



def test_divergent_daily_assessments_generate_non_executable_review_note():
    temp, engine, sessions = db()
    from app.modules.inventory.persistence_model import InventoryOperationAuditModel
    with sessions.begin() as session:
        for day, conclusion in ((10, "Warehouse code is in B"), (11, "Warehouse code is in C")):
            session.add(InventoryOperationAuditModel(
                tenant_id="tenant-a", business_date=date(2026, 10, day),
                stage="evening_reconcile", status="completed", writes=0,
                summary_json={"verification_status": "verified", "read_cells": 14},
                assessment_json={
                    "observations": [{
                        "code": "WAREHOUSE_COLUMN",
                        "conclusion": conclusion, "confidence": 0.9,
                        "evidence": [{"sheet": "Kho", "cell": "B2",
                                      "evidence_hash": "b"*64}],
                    }],
                },
            ))
    rows = InventoryKnowledgeService(sessions).learn_from_verified_assessments("tenant-a")
    assert len(rows) == 1
    assert rows[0]["status"] == "proposed"
    assert rows[0]["structured_rule"]["distinct_conclusions"] == 2
    assert rows[0]["structured_rule"]["executable"] is False
    assert "review" in rows[0]["content"].lower()
    engine.dispose(); temp.cleanup()


def test_assessment_learning_skips_failed_unverified_and_ungrounded_observations():
    temp, engine, sessions = db()
    knowledge = InventoryKnowledgeService(sessions)
    from app.modules.inventory.persistence_model import InventoryOperationAuditModel
    with sessions.begin() as session:
        for status, verification, evidence, business_day in (
            ("failed", "failed", [{"sheet": "Kho", "cell": "B2", "evidence_hash": "b"*64}], 1),
            ("completed", "unknown", [{"sheet": "Kho", "cell": "B2", "evidence_hash": "b"*64}], 2),
            ("completed", "verified", [], 3),
        ):
            session.add(InventoryOperationAuditModel(
                tenant_id="tenant-a", business_date=date(2026, 10, business_day),
                stage="evening_reconcile", status=status, writes=0,
                summary_json={"verification_status": verification, "read_cells": 20},
                assessment_json={
                    "observations": [{
                        "code": "UNSAFE", "conclusion": "Unverified structural claim",
                        "confidence": 0.99, "evidence": evidence,
                    }]
                },
            ))
    assert knowledge.learn_from_verified_assessments("tenant-a") == []
    engine.dispose(); temp.cleanup()


def test_learning_health_flags_metadata_only_zero_write_streak_but_not_grounded_noop():
    temp, engine, sessions = db()
    from app.modules.inventory.persistence_model import InventoryOperationAuditModel
    with sessions.begin() as session:
        for day in (10, 11, 12):
            session.add(InventoryOperationAuditModel(
                tenant_id="tenant-a", business_date=date(2026, 10, day),
                stage="evening_reconcile", status="completed", writes=0,
                summary_json={"verification_status": "verified", "read_cells": 865},
                assessment_json={"observations": [{
                    "code": "METADATA_AND_STRUCTURE_VERIFIED", "evidence": [{
                        "sheet": "Kho", "cell": "A1", "evidence_hash": "a"*64,
                    }],
                }]},
            ))
    service = InventoryKnowledgeService(sessions)
    health = service.learning_status("tenant-a", today=date(2026, 10, 13))["health"]
    assert "unverified_noop_streak" in health["issue_codes"]
    assert health["unverified_noop_streak"] == 3
    with sessions.begin() as session:
        session.add(InventoryOperationAuditModel(
            tenant_id="tenant-a", business_date=date(2026, 10, 13),
            stage="evening_reconcile", status="completed", writes=0,
            summary_json={"verification_status": "verified", "read_cells": 865},
            assessment_json={"observations": [{
                "code": "NO_CHANGES_VERIFIED", "confidence": 0.99,
                "evidence": [
                    {"sheet": "Kho", "cell": "C7", "evidence_hash": "a"*64},
                    {"sheet": "Kho", "cell": "D7", "evidence_hash": "b"*64},
                ],
            }]},
        ))
    health = service.learning_status("tenant-a", today=date(2026, 10, 13))["health"]
    assert "unverified_noop_streak" not in health["issue_codes"]
    assert health["unverified_noop_streak"] == 0
    engine.dispose(); temp.cleanup()


def test_learning_status_reports_verified_reads_and_last_learning_result():
    temp, engine, sessions = db()
    from app.modules.inventory.persistence_model import InventoryOperationAuditModel
    with sessions.begin() as session:
        session.add(InventoryOperationAuditModel(
            tenant_id="tenant-a", business_date=date(2026, 10, 11),
            stage="evening_reconcile", status="completed",
            summary_json={
                "read_cells": 865, "verification_status": "verified",
                "learning": {"status": "completed", "history_candidates": 0,
                             "assessment_candidates": 1},
            },
            assessment_json={"observations": [{"code": "LABEL"}]},
        ))
    data = InventoryKnowledgeService(sessions).learning_status("tenant-a")
    assert data["verified_days"] == 1
    assert data["read_cells_verified"] == 865
    assert data["last_verified_day"] == "2026-10-11"
    assert data["last_learning"]["status"] == "completed"
    assert data["last_learning"]["assessment_candidates"] == 1
    assert data["knowledge"]["active"] == 0
    engine.dispose(); temp.cleanup()


def test_historical_learner_requires_repeated_verified_changes_and_never_activates():
    temp, engine, sessions = db()
    audit_service = InventoryOperationAuditService(sessions)
    knowledge = InventoryKnowledgeService(sessions)

    def result(run_id, *, verification="verified", reason="carry closing to opening"):
        return SimpleNamespace(
            run_id=run_id, status="completed", tool_rounds=1, read_calls=1,
            read_cells=2, writes=1, plan_hash=(run_id[0] * 64),
            staged=SimpleNamespace(operations=[1], issues=[], material_actions=[], summary="learn"),
            assessment={}, tool_trace=[], ranges_read=[], business_prompt_source="default",
            business_prompt_version="v1", business_prompt_hash="h"*64,
            knowledge_hash="k"*64, knowledge_version=0, model="gemini-test",
            execution={"verification_status": verification}, knowledge_proposals=[],
            change_audit=[{
                "sheet": "Kho", "cell": "G17", "before": 0, "after": 12,
                "source_sheet": "Kho", "source_cell": "K17", "material_id": "cotton",
                "warehouse_id": "wh-1", "operation_type": "set_cell", "reason": reason,
                "provenance": "exact_copy", "evidence": [],
                "verification_status": verification,
            }],
        )

    audit_service.persist_v4_result("tenant-a", date(2026, 9, 20), stage="evening_reconcile", result=result("a"*64))
    assert knowledge.learn_from_verified_history("tenant-a") == []
    audit_service.persist_v4_result("tenant-a", date(2026, 9, 21), stage="evening_reconcile", result=result("b"*64))
    learned = knowledge.learn_from_verified_history("tenant-a")
    assert len(learned) == 1
    assert learned[0]["status"] == "proposed"
    assert learned[0]["source"] == "historical_verified_audit"
    assert learned[0]["structured_rule"]["support"] == 2
    assert learned[0]["structured_rule"]["mode"] == "shadow"
    assert learned[0]["structured_rule"]["eligible_for_activation"] is False
    assert learned[0]["structured_rule"]["conflicts"] == 0
    assert len(learned[0]["evidence"]) == 2
    assert knowledge.active_snapshot("tenant-a")["count"] == 0
    again = knowledge.learn_from_verified_history("tenant-a")
    assert again[0]["id"] == learned[0]["id"]
    engine.dispose(); temp.cleanup()


def test_historical_learner_ignores_unverified_changes():
    temp, engine, sessions = db()
    service = InventoryOperationAuditService(sessions)
    for index in range(2):
        result = SimpleNamespace(
            run_id=str(index) * 64, status="completed", tool_rounds=1, read_calls=1,
            read_cells=1, writes=1, plan_hash="p"*64,
            staged=SimpleNamespace(operations=[1], issues=[], material_actions=[], summary="x"),
            assessment={}, tool_trace=[], ranges_read=[], business_prompt_source="default",
            business_prompt_version="v1", business_prompt_hash="h"*64,
            knowledge_hash="k"*64, knowledge_version=0, model="gemini-test",
            execution={"verification_status": "unknown"}, knowledge_proposals=[],
            change_audit=[{"sheet":"Kho","cell":f"G{index+2}","before":0,"after":1,
                "operation_type":"set_cell","reason":"unverified pattern","provenance":"exact_copy",
                "evidence":[],"verification_status":"unknown"}],
        )
        service.persist_v4_result("tenant-a", date(2026, 9, 20 + index), stage="evening_reconcile", result=result)
    assert InventoryKnowledgeService(sessions).learn_from_verified_history("tenant-a") == []
    engine.dispose(); temp.cleanup()


def test_review_required_audit_exposes_actionable_issues_without_verifying_stock():
    temp, engine, sessions = db()
    issue = {
        "code": "UNMAPPED_REPORT_ONLY_MATERIAL",
        "message": "Needs operator mapping evidence",
        "requires_review": True,
        "evidence": [{"sheet": "Daily", "cell": "B9", "evidence_hash": "e" * 64}],
    }
    result = SimpleNamespace(
        run_id="a" * 64, status="review_required", tool_rounds=2,
        read_calls=2, read_cells=42, writes=0, plan_hash="p" * 64,
        staged=SimpleNamespace(operations=[], issues=[issue], material_actions=[], summary="Needs review"),
        assessment={"observations": []}, tool_trace=[], ranges_read=[],
        business_prompt_source="default", business_prompt_version="v1",
        business_prompt_hash="h" * 64, knowledge_hash="k" * 64, knowledge_version=0,
        model="gemini-test", execution={"verification_status": "not_executed"},
        knowledge_proposals=[], change_audit=[],
    )
    service = InventoryOperationAuditService(sessions)
    persisted = service.persist_v4_result("tenant-a", date(2026, 10, 20),
                                          stage="evening_reconcile", result=result)
    detail = service.stage_detail("tenant-a", date(2026, 10, 20), "evening_reconcile")
    assert detail["status"] == "review_required"
    assert detail["issues"] == [issue]
    assert detail["summary"]["issue_count"] == 1
    assert detail["writes"] == 0
    assert persisted["issues"] == [issue]
    engine.dispose()
    temp.cleanup()


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
