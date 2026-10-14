"""Codex profile manager: scoped settings, no token reads, no workload routing."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.modules.processing_policy.model import ProcessingPolicyAuditModel
from app.modules.realistic_review_ugc.codex_account_manager import (
    account_home, ensure_accounts, status, update_settings,
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
        assert [a["is_executing"] for a in results["accounts"]] == [True, False]
        assert all(a["auth_status"] == "not_connected" for a in results["accounts"])
        assert str(account_home("account-2", home)).endswith("codex-account-2")
        session.commit()
        assert len(ensure_accounts(session, "tenant-a")) == 2
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
