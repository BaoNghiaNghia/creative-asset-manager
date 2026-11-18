"""No-live-mutation guard tests for Inventory-specific Codex cutover."""
from __future__ import annotations
import json
from pathlib import Path
import pytest

from app.operations.inventory_codex_cutover import (
    PATCH, CutoverBlocked, patch_environment, restore_environment,
    parse_environment_file_from_text, write_atomic,
)


def test_only_whitelisted_inventory_fields_change_and_secrets_preserved():
    original = (
        "# CAM production config\n"
        "APP_ENV=production\n"
        "GOOGLE_CLIENT_SECRET='do-not-log-or-mutate'\n"
        "R2_VIDEO_MEDIA_SIGNING_SECRET='unrelated-secret'\n"
        "INVENTORY_AI_PROVIDER=gemini\n"
        "INVENTORY_AI_ALLOWED_MODELS=gemini-3.5-flash-lite\n"
        "CAM_ETSY_IMAGE_SHARED_R2_ENABLED=true\n"
    )
    modified = patch_environment(original, PATCH)
    assert modified.count("INVENTORY_AI_PROVIDER=") == 1
    assert modified.count("INVENTORY_CODEX_ACCOUNT=") == 1
    assert modified.count("INVENTORY_CODEX_TIMEOUT_SECONDS=") == 1
    assert "GOOGLE_CLIENT_SECRET='do-not-log-or-mutate'" in modified
    assert "R2_VIDEO_MEDIA_SIGNING_SECRET='unrelated-secret'" in modified
    assert "CAM_ETSY_IMAGE_SHARED_R2_ENABLED=true" in modified
    assert parse_environment_file_from_text(modified)["INVENTORY_AI_PROVIDER"] == "codex_cli"
    assert "gemini-3.5-flash-lite" not in parse_environment_file_from_text(modified)["INVENTORY_AI_ALLOWED_MODELS"]


def test_manual_rollback_restores_original_provider_without_exposing_secrets():
    source="APP_ENV=production\nGOOGLE_SECRET=do-not-modify\nINVENTORY_AI_PROVIDER=codex_cli\nINVENTORY_AI_ALLOWED_MODELS=codex-cli-default\nINVENTORY_CODEX_ACCOUNT=account-1\nINVENTORY_CODEX_TIMEOUT_SECONDS=80\n"
    restored = restore_environment(source, {
        "INVENTORY_AI_PROVIDER": "gemini",
        "INVENTORY_AI_ALLOWED_MODELS": "gemini-3.5-flash-lite",
        "INVENTORY_CODEX_ACCOUNT": None,
        "INVENTORY_CODEX_TIMEOUT_SECONDS": None,
    })
    assert "GOOGLE_SECRET=do-not-modify" in restored
    assert "INVENTORY_CODEX_ACCOUNT" not in restored
    assert "INVENTORY_CODEX_TIMEOUT_SECONDS" not in restored
    assert parse_environment_file_from_text(restored)["INVENTORY_AI_PROVIDER"] == "gemini"


def test_env_duplicate_or_invalid_provider_patch_fails_closed():
    with pytest.raises(CutoverBlocked, match="duplicate_env_key"):
        patch_environment("INVENTORY_AI_PROVIDER=gemini\nINVENTORY_AI_PROVIDER=gemini\n", PATCH)
    with pytest.raises(CutoverBlocked, match="invalid_patch"):
        patch_environment("INVENTORY_AI_PROVIDER=gemini\n", {"INVENTORY_AI_PROVIDER": "a bad value"})


def test_atomic_file_preserves_permissions_and_unrelated_values(tmp_path):
    original = tmp_path / "production.env"
    original.write_text("APP_ENV=production\nAPI_TOKEN=unchanged\n")
    original.chmod(0o640)
    write_atomic(original, patch_environment(original.read_text(), PATCH))
    assert original.stat().st_mode & 0o777 == 0o640
    assert parse_environment_file_from_text(original.read_text())["API_TOKEN"] == "unchanged"


def test_no_secrets_in_cutover_rule_metadata():
    assert set(PATCH) == {
        "INVENTORY_AI_PROVIDER", "INVENTORY_AI_ALLOWED_MODELS",
        "INVENTORY_CODEX_ACCOUNT", "INVENTORY_CODEX_TIMEOUT_SECONDS",
    }
    assert PATCH["INVENTORY_CODEX_ACCOUNT"] == "account-1"
    assert PATCH["INVENTORY_AI_ALLOWED_MODELS"] == "codex-cli-default"
