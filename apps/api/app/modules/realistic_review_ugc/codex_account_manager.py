"""Admin-only Codex profile registry and Stage 2 worker slot allocation.

Secondary Codex profiles retain isolated authentication, and Stage 2 routing
only selects them after successful authentication and skill verification.
"""
from __future__ import annotations

import os
import fcntl
import shutil
import subprocess
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.model import RrugcCodexAccountConfigModel
from app.modules.processing_policy.model import ProcessingPolicyAuditModel

PROFILE_IDS = ("account-1", "account-2")
STAGES = ("stage1", "stage2", "stage4")
MAX_PROFILES = 2


@contextmanager
def acquire_worker_profile(
    session: Session,
    tenant_id: str,
    stage: str,
    primary_home: str,
    binary: str = "codex",
    skill_name: str | None = None,
):
    """Hold a cross-process profile slot until its Codex invocation finishes."""
    if stage not in STAGES:
        raise ValueError("Unsupported Codex stage")
    records = ensure_accounts(session, tenant_id)
    candidates = [row for row in records if row.enabled and stage in (row.stages_json or [])]
    # Prefer authenticated secondary capacity, falling back to the original profile.
    candidates.sort(key=lambda row: row.account_id != "account-2")
    for record in candidates:
        if record.account_id == "account-2" and auth_status(
            record.account_id, primary_home, binary
        ) != "connected":
            continue
        home = account_home(record.account_id, primary_home)
        if skill_name and record.account_id == "account-2" and not (
            home / "skills" / skill_name / "SKILL.md"
        ).is_file():
            continue
        lock_dir = home / "worker-locks"
        try:
            lock_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError:
            continue
        for slot in range(max(1, min(record.max_concurrent, 3))):
            handle = None
            try:
                handle = (lock_dir / f"slot-{slot}.lock").open("a+")
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                if handle is not None:
                    handle.close()
                continue
            try:
                yield record.account_id, str(home)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()
            return
    raise RuntimeError("codex_account_pool_busy")


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
    secondary = next(row for row in records if row.account_id == "account-2")
    stage2_pool_ready = bool(
        secondary.enabled
        and "stage2" in (secondary.stages_json or [])
        and auth_status("account-2", primary_home, binary) == "connected"
    )
    return {
        "routing_mode": "stage2_pool" if stage2_pool_ready else "single_active",
        "routing_note": (
            "Stage 2 can dispatch to both eligible Codex profiles. "
            "Stage 1 and Stage 4 still use the existing execution path."
            if stage2_pool_ready else
            "Stage 2 uses Account 01 until Account 02 is enabled and authenticated. "
            "Stage 1 and Stage 4 are not pooled yet."
        ),
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
