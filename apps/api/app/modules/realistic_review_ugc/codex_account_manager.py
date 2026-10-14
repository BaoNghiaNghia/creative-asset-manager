"""Tenant-configured, cross-process Codex account pool for RRUGC image stages.

Authentication is isolated per profile; shared managed skills contain no tokens.
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


class CodexAccountPoolBusy(RuntimeError):
    """All eligible worker profile slots are currently reserved."""


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
        if skill_name and record.account_id == "account-2":
            skill_dir = home / "skills" / skill_name
            if not (skill_dir / "SKILL.md").is_file():
                continue
            # Stage 1 now requires the actual licensed font assets, not
            # merely a copy of SKILL.md, to avoid mislabeled font reports.
            if skill_name == "gatorhats-stage1-six-designs" and not all(
                (skill_dir / "fonts" / filename).is_file()
                for filename in ("BebasNeue-Regular.ttf", "Pacifico-Regular.ttf", "Lobster-Regular.otf")
            ):
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
    raise CodexAccountPoolBusy("codex_account_pool_busy")


def active_slots(account_id: str, primary_home: str, limit: int = 3) -> int:
    """Count currently locked process slots without reading worker credentials."""
    lock_dir = account_home(account_id, primary_home) / "worker-locks"
    active = 0
    for slot in range(max(1, min(limit, 3))):
        lock_path = lock_dir / f"slot-{slot}.lock"
        if not lock_path.is_file():
            continue
        try:
            with lock_path.open("r") as handle:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    active += 1
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            continue
    return active


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
    accounts = [
        {
            "account_id": row.account_id,
            "display_name": row.display_name,
            "enabled": row.enabled,
            "max_concurrent": row.max_concurrent,
            "stages": row.stages_json,
            "auth_status": auth_status(row.account_id, primary_home, binary),
            "active_jobs": active_slots(row.account_id, primary_home),
            "updated_at": row.updated_at,
        }
        for row in records
    ]
    for account in accounts:
        account["is_executing"] = account["active_jobs"] > 0
    secondary = next(account for account in accounts if account["account_id"] == "account-2")
    eligible_stages = [
        stage for stage in STAGES
        if secondary["enabled"] and secondary["auth_status"] == "connected"
        and stage in secondary["stages"]
    ]
    return {
        "routing_mode": "multi_account" if eligible_stages else "single_active",
        "routing_note": (
            "Account 02 is eligible for " + ", ".join(eligible_stages)
            + " when the selected skill is installed. Jobs use available profile slots."
            if eligible_stages else
            "Account 01 handles eligible jobs until Account 02 is enabled, authenticated and configured for a stage."
        ),
        "accounts": accounts,
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
