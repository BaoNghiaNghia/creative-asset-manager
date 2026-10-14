"""Codex account pool: scoped settings, shared process locks and safe failover."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.modules.processing_policy.model import ProcessingPolicyAuditModel
from app.modules.realistic_review_ugc.codex_account_manager import (
    account_home, acquire_worker_profile, ensure_accounts, status, update_settings,
)
from app.modules.realistic_review_ugc.model import RrugcCodexAccountConfigModel
from app.modules.realistic_review_ugc.router import CodexAccountSettingsRequest, save_codex_account_settings


def db():
    engine = create_engine("sqlite://")
    RrugcCodexAccountConfigModel.__table__.create(engine)
    ProcessingPolicyAuditModel.__table__.create(engine)
    return engine


def test_default_profile_is_preserved_and_secondary_is_never_active(tmp_path: Path):
    engine = db()
    home = str(tmp_path / "codex")
    with Session(engine) as session:
        results = status(session, "tenant-a", home)
        assert results["routing_mode"] == "single_active"
        assert [a["account_id"] for a in results["accounts"]] == ["account-1", "account-2"]
        assert [a["enabled"] for a in results["accounts"]] == [True, False]
        assert [a["is_executing"] for a in results["accounts"]] == [False, False]
        assert [a["active_jobs"] for a in results["accounts"]] == [0, 0]
        assert all(a["auth_status"] == "not_connected" for a in results["accounts"])
        assert str(account_home("account-2", home)).endswith("codex-account-2")
        session.commit()
        assert len(ensure_accounts(session, "tenant-a")) == 2
    engine.dispose()


def test_pool_locks_slots_and_falls_back_without_secondary_auth(tmp_path: Path):
    engine = db()
    home = str(tmp_path / "codex")
    with Session(engine) as session:
        ensure_accounts(session, "tenant-a")
        session.commit()
        with acquire_worker_profile(session, "tenant-a", "stage2", home) as (account, selected):
            assert account == "account-1"
            assert selected == str(tmp_path / "codex")
            with pytest.raises(RuntimeError, match="codex_account_pool_busy"):
                with acquire_worker_profile(session, "tenant-a", "stage2", home):
                    pass
        with acquire_worker_profile(session, "tenant-a", "stage2", home) as (account, _):
            assert account == "account-1"
    engine.dispose()


def test_secondary_stage_routing_and_live_slot_count(tmp_path: Path, monkeypatch):
    engine = db()
    primary = str(tmp_path / "codex")
    secondary = account_home("account-2", primary)
    (secondary / "skills" / "managed-skill").mkdir(parents=True)
    (secondary / "skills" / "managed-skill" / "SKILL.md").write_text("managed")
    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.codex_account_manager.auth_status",
        lambda account_id, home, binary="codex": "connected",
    )
    with Session(engine) as session:
        ensure_accounts(session, "tenant-a")
        update_settings(
            session, tenant_id="tenant-a", account_id="account-2", actor="admin",
            display_name="Second", enabled=True, max_concurrent=1,
            stages=["stage1", "stage4"],
        )
        active = status(session, "tenant-a", primary)
        assert active["routing_mode"] == "multi_account"
        assert "stage1" in active["routing_note"]
        with acquire_worker_profile(
            session, "tenant-a", "stage1", primary, skill_name="managed-skill",
        ) as (account, selected):
            assert account == "account-2" and selected == str(secondary)
            accounts = status(session, "tenant-a", primary)["accounts"]
            assert [item["active_jobs"] for item in accounts] == [0, 1]
            # Secondary slot is full: another Stage 1 job falls back to Account 01.
            with acquire_worker_profile(
                session, "tenant-a", "stage1", primary, skill_name="managed-skill",
            ) as (fallback, _):
                assert fallback == "account-1"
            # A Stage 2 job is excluded from the secondary profile.
            with acquire_worker_profile(
                session, "tenant-a", "stage2", primary, skill_name="managed-skill",
            ) as (stage2, _):
                assert stage2 == "account-1"
        assert [item["active_jobs"] for item in status(session, "tenant-a", primary)["accounts"]] == [0, 0]
    engine.dispose()


def test_tenant_settings_isolation_and_non_secret_audit(tmp_path: Path):
    engine = db()
    with Session(engine) as session:
        ensure_accounts(session, "tenant-a")
        ensure_accounts(session, "tenant-b")
        update_settings(
            session, tenant_id="tenant-a", account_id="account-2",
            actor="admin-a", display_name="Secondary profile",
            enabled=True, max_concurrent=2, stages=["stage1", "stage4"],
        )
        account_a = session.get(RrugcCodexAccountConfigModel, ("tenant-a", "account-2"))
        account_b = session.get(RrugcCodexAccountConfigModel, ("tenant-b", "account-2"))
        assert account_a.enabled is True and account_b.enabled is False
        assert account_a.max_concurrent == 2 and account_b.max_concurrent == 1
        audits = session.scalars(select(ProcessingPolicyAuditModel)).all()
        assert len(audits) == 1
        assert audits[0].actor_id == "admin-a"
        assert audits[0].new_policy_json["account_id"] == "account-2"
        assert "auth" not in str(audits[0].new_policy_json).lower()
    engine.dispose()


def test_profile_validation_and_administrator_gate(tmp_path: Path):
    engine = db()
    with Session(engine) as session:
        request = CodexAccountSettingsRequest(
            display_name="Secondary", enabled=True,
            max_concurrent=2, stages=["stage1"],
        )
        principal = SimpleNamespace(
            active_tenant_id="tenant-a", platform_admin=False,
            effective_permissions={"realistic_review_ugc.configure"},
            user_id="user-a",
        )
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as error:
            save_codex_account_settings("account-2", request, session=session, principal=principal)
        assert error.value.status_code == 403
        assert session.query(RrugcCodexAccountConfigModel).count() == 0
        with pytest.raises(ValueError):
            update_settings(
                session, tenant_id="tenant-a", account_id="unknown", actor="admin",
                display_name="Unknown", enabled=True, max_concurrent=1,
                stages=["stage1"],
            )
        with pytest.raises(ValueError):
            update_settings(
                session, tenant_id="tenant-a", account_id="account-2", actor="admin",
                display_name="Secondary", enabled=True, max_concurrent=6,
                stages=["stage1"],
            )
    engine.dispose()
