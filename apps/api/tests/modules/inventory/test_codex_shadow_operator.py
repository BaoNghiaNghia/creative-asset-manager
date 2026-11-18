"""Offline tests for the operator-only Codex Inventory login gate."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.operations import inventory_codex_shadow as runner


def test_existing_profiles_reuse_cam_codex_home_without_copying_credentials(tmp_path):
    settings = SimpleNamespace(CODEX_IMAGE_HOME=str(tmp_path / "codex"))
    assert runner.existing_profile("account-1", settings) == (tmp_path / "codex").resolve()
    assert runner.existing_profile("account-2", settings) == (tmp_path / "codex-account-2").resolve()
    with pytest.raises(ValueError, match="inventory_codex_unknown_account"):
        runner.existing_profile("account-3", settings)


def test_smoke_uses_managed_account_one_with_synthetic_evidence(
    tmp_path, monkeypatch, capsys,
):
    import json
    import sys
    captured = {}
    monkeypatch.setattr(
        runner, "Settings",
        lambda **kw: SimpleNamespace(
            CODEX_IMAGE_HOME=str(tmp_path / "codex"),
            CODEX_IMAGE_BINARY="/usr/bin/codex",
        ),
    )
    monkeypatch.setattr(runner, "preflight", lambda home, binary: {
        "engine": "codex_cli", "mode": "shadow", "ready": True, "reason": "ready",
    })

    def fake_review(data, **kwargs):
        captured["data"] = data
        captured["kwargs"] = kwargs
        return {
            "engine": "codex_cli", "apply_mode": "shadow",
            "verification_status": "review_required",
            "review": {"summary": "test", "findings": [], "unresolved": []},
            "writes": 0,
        }

    monkeypatch.setattr(runner, "review_inventory_evidence", fake_review)
    monkeypatch.setattr(sys, "argv", [
        "inventory_codex_shadow", "smoke", "--account", "account-1",
    ])
    assert runner.main() == 0
    public = json.loads(capsys.readouterr().out)
    assert public["ready"] is True
    assert public["account"] == "account-1"
    assert public["scenario"] == "synthetic_evidence"
    assert public["writes"] == 0
    assert captured["kwargs"]["codex_home"].endswith("/codex")
    assert captured["data"]["cells"][0]["sheet"] == "SYNTHETIC_NO_REAL_DATA"


def test_tools_smoke_two_rounds_never_touches_google_or_inventory_db(
    tmp_path, monkeypatch, capsys,
):
    import json
    import sys
    from app.modules.inventory.ai.gateway import InventoryGeminiToolCall, InventoryGeminiToolTurn

    monkeypatch.setattr(runner, "Settings", lambda **k: SimpleNamespace(
        CODEX_IMAGE_HOME=str(tmp_path / "codex"),
        CODEX_IMAGE_BINARY="/usr/bin/codex",
        INVENTORY_CODEX_TIMEOUT_SECONDS=80,
    ))
    monkeypatch.setattr(runner, "preflight", lambda home, binary: {
        "engine": "codex_cli", "mode": "shadow", "ready": True, "reason": "ready",
    })
    seen = []
    class FakeGateway:
        def __init__(self, *args, **kwargs): pass
        def generate_tool_turn(self, **kwargs):
            seen.append(kwargs)
            if len(seen) == 1:
                return InventoryGeminiToolTurn(
                    content={"role":"model","parts":[]},
                    calls=(InventoryGeminiToolCall(
                        name="inspect_inventory_evidence",
                        arguments={"reference": "SYNTHETIC_ONLY_NO_GOOGLE_SHEETS"},
                    ),),
                )
            return InventoryGeminiToolTurn(
                content={"role":"model","parts":[]},
                calls=(InventoryGeminiToolCall(
                    name="submit_review",
                    arguments={"status":"review_required","evidence":"not verified"},
                ),),
            )
    import app.modules.inventory.ai.codex_cli_gateway as cli
    monkeypatch.setattr(cli, "RuntimeInventoryCodexGateway", FakeGateway)
    monkeypatch.setattr(sys, "argv", ["inventory_codex_shadow", "tools-smoke"])
    assert runner.main() == 0
    result=json.loads(capsys.readouterr().out)
    assert result["ready"] is True and result["writes"] == 0
    assert result["tool_rounds"] == 2
    assert seen[0]["force_function_name"] == "inspect_inventory_evidence"
    assert seen[1]["force_function_name"] == "submit_review"
    assert len(seen[1]["contents"]) == 3


def test_preflight_rejects_root_or_shared_identity(tmp_path, monkeypatch):
    binary = tmp_path / "codex"
    binary.write_bytes(b"stub")
    binary.chmod(0o700)
    monkeypatch.setattr(runner.os, "geteuid", lambda: 0)
    result = runner.preflight(tmp_path, binary)
    assert result["ready"] is False
    assert result["reason"] == "dedicated_service_identity_unavailable"


def test_preflight_never_prints_auth_output(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    binary = tmp_path / "codex"
    binary.write_bytes(b"stub")
    binary.chmod(0o700)
    monkeypatch.setattr(runner.os, "geteuid", lambda: 4242)
    original_stat = Path.stat
    def fake_stat(self, *args, **kwargs):
        result = original_stat(self, *args, **kwargs)
        if self == home:
            return SimpleNamespace(st_uid=4242, st_mode=result.st_mode)
        return result
    monkeypatch.setattr(Path, "stat", fake_stat)
    called = {}

    def fake_run(cmd, **kwargs):
        called["cmd"], called["kwargs"] = cmd, kwargs
        # Current CLI writes the success message to stderr, not stdout.
        return SimpleNamespace(returncode=0, stdout=b"")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    value = runner.preflight(home, binary)
    assert value == {
        "engine": "codex_cli", "mode": "shadow", "ready": True, "reason": "ready",
    }
    assert called["cmd"] == [str(binary), "login", "status"]
    assert set(called["kwargs"]["env"]).isdisjoint({"DATABASE_URL", "OPENAI_API_KEY"})
    assert "sensitive@example.net" not in str(value)


def test_preflight_does_not_consider_failed_login_authenticated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    binary = tmp_path / "codex"
    binary.write_bytes(b"stub")
    binary.chmod(0o700)
    monkeypatch.setattr(runner.os, "geteuid", lambda: 4242)
    original_stat = Path.stat
    def fake_stat(self, *args, **kwargs):
        result = original_stat(self, *args, **kwargs)
        if self == home:
            return SimpleNamespace(st_uid=4242, st_mode=result.st_mode)
        return result
    monkeypatch.setattr(Path, "stat", fake_stat)
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k:
                        SimpleNamespace(returncode=1, stdout=b"not logged in"))
    value = runner.preflight(home, binary)
    assert value["ready"] is False
    assert value["reason"] == "codex_service_login_required"
