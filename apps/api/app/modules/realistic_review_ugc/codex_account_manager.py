"""Admin-only Codex profile registry.

This module intentionally does not distribute jobs between accounts.
Current Codex workers still use the original CODEX_IMAGE_HOME. Any secondary
profile is configured independently and must be explicitly authenticated by
its owner before a separately reviewed scheduler can be enabled.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.model import RrugcCodexAccountConfigModel
from app.modules.processing_policy.model import ProcessingPolicyAuditModel

PROFILE_IDS = ("account-1", "account-2")
STAGES = ("stage1", "stage2", "stage4")
MAX_PROFILES = 2


def account_home(account_id: str, primary_home: str) -> Path:
    root = Path(primary_home).resolve()
    if account_id == "account-1":
        return root
    if account_id == "account-2":
        return root.parent / f"{root.name}-account-2"
    raise ValueError("Unknown Codex account")


def ensure_accounts(session: Session, tenant_id: str) -> list[RrugcCodexAccountConfigModel]:
    found = {
        row.account_id: row for row in session.scalars(
            select(RrugcCodexAccountConfigModel).where(
                RrugcCodexAccountConfigModel.tenant_id == tenant_id,
            )
        )
    }
    for account_id in PROFILE_IDS:
        if account_id not in found:
            entry = RrugcCodexAccountConfigModel(
                tenant_id=tenant_id, account_id=account_id,
                display_name="Account 01 · Existing" if account_id == "account-1" else "Account 02",
                enabled=account_id == "account-1",
                max_concurrent=1,
                stages_json=list(STAGES),
                updated_at=datetime.now(timezone.utc),
            )
            session.add(entry)
            found[account_id] = entry
    session.flush()
    return [found[key] for key in PROFILE_IDS]


def auth_status(account_id: str, primary_home: str, binary: str = "codex") -> str:
    home = account_home(account_id, primary_home)
    if not (home / "auth.json").is_file():
        return "not_connected"
    command = shutil.which(binary)
    if not command:
        return "cli_unavailable"
    environment = dict(os.environ)
    for name in (
        "OPENAI_API_KEY", "OPENAI_ORGANIZATION", "OPENAI_PROJECT",
        "OPENAI_BASE_URL", "CODEX_API_KEY", "OPENAI_EXECUTOR_API_KEY",
    ):
        environment.pop(name, None)
    environment["CODEX_HOME"] = str(home)
    try:
        status = subprocess.run(
            [command, "login", "status"], env=environment, check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "check_failed"
    return "connected" if status.returncode == 0 else "auth_required"


def status(session: Session, tenant_id: str, primary_home: str, binary: str = "codex") -> dict:
    records = ensure_accounts(session, tenant_id)
    return {
        "routing_mode": "single_active",
        "routing_note": "Codex workers currently use Account 01. Account 02 is configured but is not a live worker pool member.",
        "accounts": [
            {
                "account_id": row.account_id,
                "display_name": row.display_name,
                "enabled": row.enabled,
                "max_concurrent": row.max_concurrent,
                "stages": row.stages_json,
                "auth_status": auth_status(row.account_id, primary_home, binary),
                "is_executing": row.account_id == "account-1",
                "updated_at": row.updated_at,
            }
            for row in records
        ],
    }


def update_settings(
    session: Session, *,
    tenant_id: str, account_id: str, actor: str,
    display_name: str, enabled: bool, max_concurrent: int,
    stages: list[str],
) -> None:
    if account_id not in PROFILE_IDS:
        raise ValueError("Unknown Codex account")
    if not 1 <= max_concurrent <= 3 or not set(stages).issubset(STAGES):
        raise ValueError("Invalid Codex configuration")
    ensure_accounts(session, tenant_id)
    row = session.get(RrugcCodexAccountConfigModel, (tenant_id, account_id))
    if row is None:
        raise ValueError("Unknown Codex account")
    previous = {
        "display_name": row.display_name, "enabled": row.enabled,
        "max_concurrent": row.max_concurrent, "stages": row.stages_json,
    }
    row.display_name = display_name.strip()[:80]
    row.enabled = enabled
    row.max_concurrent = max_concurrent
    row.stages_json = sorted(set(stages))
    row.updated_by = actor
    row.updated_at = datetime.now(timezone.utc)
    session.add(ProcessingPolicyAuditModel(
        tenant_id=tenant_id, actor_id=actor,
        action="rrugc_codex_account_config_updated",
        reason=f"Codex profile settings updated for {account_id}",
        old_policy_json={"account_id": account_id, **previous},
        new_policy_json={
            "account_id": account_id, "display_name": row.display_name,
            "enabled": row.enabled, "max_concurrent": row.max_concurrent,
            "stages": row.stages_json,
        },
    ))
    session.commit()
