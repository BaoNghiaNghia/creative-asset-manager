"""Guarded Inventory-only Gemini -> existing ChatGPT Codex CLI cutover.

No file/source artwork is modified. Legacy Gemini copy file IDs and historical
audit remain intact. One named tenant is changed, never Creative/GatorHats AI.
Explicit rollback restores the old nonsecret environment keys and model list.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))
from deploy.tools.production_env import (
    SafeConfigurationError, parse_environment_file, validate_file_security,
    validate_application_settings,
)

ENV = Path("/etc/creative-asset-manager/production.env")
STATE = Path("/etc/creative-asset-manager/inventory-codex-cutover-state.json")
TENANT = "creative-assets-prod"
PROVIDER = "codex_cli"
MODEL = "codex-cli-default"
PATCH = {
    "INVENTORY_AI_PROVIDER": PROVIDER,
    "INVENTORY_AI_ALLOWED_MODELS": MODEL,
    "INVENTORY_CODEX_ACCOUNT": "account-1",
    "INVENTORY_CODEX_TIMEOUT_SECONDS": "80",
}
TIMER = "creative-asset-manager-inventory-v5j-lifecycle.timer"
SERVICE = "creative-asset-manager-inventory-v5j-lifecycle.service"
RESTARTS = (
    "creative-asset-manager-api.service",
    "creative-asset-manager-image-worker.service",
    "creative-asset-manager-image-worker-2.service",
    "creative-asset-manager-image-worker-3.service",
    "creative-asset-manager-image-worker-4.service",
    "creative-asset-manager-inventory-worker.service",
)


class CutoverBlocked(Exception):
    pass


def patch_environment(source: str, replacement: dict[str, str]) -> str:
    """Preserve all existing unrelated env secrets and comments unchanged."""
    if any(not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or
           not re.fullmatch(r"[A-Za-z0-9_.-]{1,85}", value)
           for key, value in replacement.items()):
        raise CutoverBlocked("invalid_patch")
    seen = set()
    output = []
    for raw in source.splitlines(keepends=True):
        if raw.lstrip().startswith("#") or not raw.strip() or "=" not in raw:
            output.append(raw)
            continue
        key = raw.split("=", 1)[0].strip()
        if key not in replacement:
            output.append(raw)
            continue
        if key in seen:
            raise CutoverBlocked("duplicate_env_key")
        seen.add(key)
        newline = "\r\n" if raw.endswith("\r\n") else "\n"
        output.append(f"{key}={replacement[key]}{newline}")
    if output and not output[-1].endswith("\n"):
        output[-1] += "\n"
    for key, value in replacement.items():
        if key not in seen:
            output.append(f"{key}={value}\n")
    return "".join(output)


def write_atomic(path: Path, content: str, *, mode: int | None = None) -> None:
    if path.is_symlink():
        raise CutoverBlocked("symlink_target_rejected")
    metadata = path.stat() if path.exists() else None
    if mode is None:
        mode = stat.S_IMODE(metadata.st_mode) if metadata else 0o600
    descriptor, location = tempfile.mkstemp(prefix=".inventory-cutover-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            os.fchmod(output.fileno(), mode)
            if metadata is not None:
                os.fchown(output.fileno(), metadata.st_uid, metadata.st_gid)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(location, path)
    finally:
        if os.path.exists(location):
            os.unlink(location)


def run_systemctl(action: str, unit: str) -> bool:
    if action not in {"is-active", "stop", "start", "try-restart"} or not unit.startswith(
        "creative-asset-manager-"
    ) or not unit.endswith((".timer", ".service")):
        raise CutoverBlocked("unexpected_systemd_operation")
    try:
        result = subprocess.run(["systemctl", action, unit], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=45, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise CutoverBlocked("systemd_unavailable") from None
    return result.returncode == 0


def restart_providers() -> None:
    for unit in RESTARTS:
        if run_systemctl("is-active", unit) and not run_systemctl("try-restart", unit):
            raise CutoverBlocked("inventory_service_restart_failed")


def db_control(tenant: str, *, new_provider: str | None = None,
               new_models: list[str] | None = None, expect: str | None = None):
    from sqlalchemy import select
    from app.core.database import SessionLocal
    from app.modules.inventory.model import InventoryAiControlModel
    with SessionLocal.begin() as session:
        query = select(InventoryAiControlModel).where(
            InventoryAiControlModel.tenant_id == tenant,
        )
        if session.bind.dialect.name == "postgresql":
            query = query.with_for_update()
        row = session.scalar(query)
        if row is None:
            raise CutoverBlocked("inventory_control_missing")
        if expect and row.provider != expect:
            raise CutoverBlocked("inventory_provider_drift")
        previous = {"provider": row.provider, "models": list(row.allowed_models_json or []),
                    "enabled": bool(row.enabled), "emergency_stop": bool(row.emergency_stop)}
        if new_provider:
            if not previous["enabled"] or previous["emergency_stop"]:
                raise CutoverBlocked("inventory_ai_not_active")
            row.provider = new_provider
            row.allowed_models_json = list(new_models or [])
        return previous


def _settings(env_file: Path):
    validate_file_security(env_file, expected_owner_uid=0)
    env = parse_environment_file(env_file)
    if env.get("APP_ENV", "").lower() not in {"production", "prod"}:
        raise CutoverBlocked("environment_not_production")
    os.environ.update(env)
    return env


def _readiness(tenant: str, values: dict[str, str]) -> dict[str, object]:
    from app.core.config import Settings
    settings = Settings(_env_file=None)
    control = db_control(tenant)
    return {
        "tenant": tenant,
        "deployment_provider": values.get("INVENTORY_AI_PROVIDER", ""),
        "deployment_allowed_models": values.get("INVENTORY_AI_ALLOWED_MODELS", ""),
        "tenant_provider": control["provider"],
        "tenant_models": control["models"],
        "ai_enabled": settings.INVENTORY_AI_ENABLED and control["enabled"],
        "emergency_stop": control["emergency_stop"],
        "codex_source_present": (REPO_ROOT / "apps/api/app/modules/inventory/ai/codex_cli_gateway.py").is_file(),
        "cutover_state_exists": STATE.exists(),
    }


def activate(tenant: str, env_file: Path) -> dict:
    if os.geteuid() != 0:
        raise CutoverBlocked("operator_root_required")
    if STATE.exists():
        raise CutoverBlocked("existing_cutover_state_requires_review")
    values = _settings(env_file)
    if values.get("INVENTORY_AI_PROVIDER") != "gemini":
        raise CutoverBlocked("current_provider_not_gemini")
    current = db_control(tenant)
    if current["provider"] != "gemini":
        raise CutoverBlocked("tenant_provider_mismatch")
    if not current["enabled"] or current["emergency_stop"]:
        raise CutoverBlocked("inventory_ai_not_active")
    if not (REPO_ROOT / "apps/api/app/modules/inventory/ai/codex_cli_gateway.py").is_file():
        raise CutoverBlocked("codex_gateway_not_deployed")
    original = env_file.read_text(encoding="utf-8")
    before = hashlib.sha256(original.encode("utf-8")).hexdigest()
    candidate = patch_environment(original, PATCH)
    candidate_values = parse_environment_file_from_text(candidate)
    validate_application_settings(candidate_values, REPO_ROOT / "apps/api")
    if not run_systemctl("stop", TIMER):
        raise CutoverBlocked("inventory_timer_stop_failed")
    try:
        if run_systemctl("is-active", SERVICE):
            raise CutoverBlocked("inventory_cycle_already_running")
        # Rollback metadata contains ONLY nonsecret provider/model settings.
        old_keys = {key: values.get(key) for key in PATCH}
        state = {
            "tenant": tenant, "previous_env": old_keys,
            "previous_control": current,
            "before_env_sha256": before,
            "activated_at": datetime.now(timezone.utc).isoformat(),
        }
        write_atomic(STATE, json.dumps(state, sort_keys=True), mode=0o600)
        db_control(tenant, new_provider=PROVIDER, new_models=[MODEL], expect="gemini")
        write_atomic(env_file, candidate)
        if parse_environment_file(env_file).get("INVENTORY_AI_PROVIDER") != PROVIDER:
            raise CutoverBlocked("env_activation_not_confirmed")
        restart_providers()
        after = db_control(tenant)
        if after["provider"] != PROVIDER or after["models"] != [MODEL]:
            raise CutoverBlocked("tenant_activation_not_confirmed")
    except Exception as exc:
        # On any failure, restore previous provider and env atomically.
        try:
            db_control(tenant, new_provider=current["provider"],
                       new_models=current["models"])
            write_atomic(env_file, original)
            restart_providers()
            STATE.unlink(missing_ok=True)
        except Exception:
            raise CutoverBlocked("cutover_rollback_requires_operator") from exc
        if isinstance(exc, CutoverBlocked):
            raise
        raise CutoverBlocked("cutover_activation_failed") from exc
    finally:
        if not run_systemctl("start", TIMER):
            raise CutoverBlocked("inventory_timer_restore_failed")
    return {"status": "active", "provider": PROVIDER, "model": MODEL,
            "account": "account-1", "tenant": tenant, "gemini_fallback": False}


def restore_environment(source: str, previous: dict[str, str | None]) -> str:
    """Revert only known provider settings; leave unrelated secrets untouched."""
    keep = {key: value for key, value in previous.items() if value is not None}
    updated = patch_environment(source, keep)
    removed = {key for key, value in previous.items() if value is None}
    return "".join(
        line for line in updated.splitlines(keepends=True)
        if line.strip().startswith("#") or
        line.split("=", 1)[0].strip() not in removed
    )


def rollback(tenant: str, env_file: Path) -> dict:
    """Explicit operator rollback, not silent provider fallback."""
    if os.geteuid() != 0:
        raise CutoverBlocked("operator_root_required")
    if not STATE.is_file() or STATE.is_symlink():
        raise CutoverBlocked("cutover_state_missing")
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CutoverBlocked("cutover_state_invalid") from exc
    if state.get("tenant") != tenant:
        raise CutoverBlocked("cutover_state_tenant_mismatch")
    old_env = state.get("previous_env")
    old_control = state.get("previous_control")
    if not isinstance(old_env, dict) or set(old_env) != set(PATCH) or any(
        x is not None and not isinstance(x, str) for x in old_env.values()
    ) or not isinstance(old_control, dict) or old_control.get("provider") != "gemini":
        raise CutoverBlocked("cutover_state_invalid")
    values = _settings(env_file)
    if values.get("INVENTORY_AI_PROVIDER") != PROVIDER:
        raise CutoverBlocked("current_provider_not_codex")
    current = db_control(tenant)
    if current["provider"] != PROVIDER:
        raise CutoverBlocked("tenant_provider_drift")
    if not run_systemctl("stop", TIMER):
        raise CutoverBlocked("inventory_timer_stop_failed")
    try:
        if run_systemctl("is-active", SERVICE):
            raise CutoverBlocked("inventory_cycle_already_running")
        original = env_file.read_text(encoding="utf-8")
        restored = restore_environment(original, old_env)
        db_control(tenant, new_provider=old_control["provider"],
                   new_models=old_control.get("models") or [], expect=PROVIDER)
        write_atomic(env_file, restored)
        restart_providers()
        STATE.unlink(missing_ok=True)
    except Exception as exc:
        try:
            db_control(tenant, new_provider=PROVIDER, new_models=[MODEL])
            write_atomic(env_file, original)
            restart_providers()
        except Exception:
            raise CutoverBlocked("rollback_recovery_requires_operator") from exc
        if isinstance(exc, CutoverBlocked):
            raise
        raise CutoverBlocked("rollback_failed") from exc
    finally:
        if not run_systemctl("start", TIMER):
            raise CutoverBlocked("inventory_timer_restore_failed")
    return {"status": "rolled_back", "tenant": tenant,
            "provider": old_control["provider"]}


def parse_environment_file_from_text(text: str) -> dict[str, str]:
    # Identical rules as production_env; no expansion, no eval, no command substitution.
    with tempfile.TemporaryDirectory(prefix="cam-env-validate-") as folder:
        path = Path(folder) / "env"
        path.write_text(text, encoding="utf-8")
        return parse_environment_file(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "activate", "rollback"))
    parser.add_argument("--tenant", default=TENANT)
    parser.add_argument("--env-file", type=Path, default=ENV)
    args = parser.parse_args()
    try:
        if args.tenant != TENANT:
            raise CutoverBlocked("unexpected_tenant")
        values = _settings(args.env_file)
        if args.action == "check":
            result = _readiness(args.tenant, values)
        elif args.action == "activate":
            result = activate(args.tenant, args.env_file)
        else:
            result = rollback(args.tenant, args.env_file)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (CutoverBlocked, SafeConfigurationError, OSError, ValueError) as exc:
        reason = str(exc) if isinstance(exc, CutoverBlocked) else "invalid_configuration"
        print(json.dumps({"status": "blocked", "reason": reason}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
