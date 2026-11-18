"""Offline tests for the operator-only Codex Inventory login gate."""
from pathlib import Path
from types import SimpleNamespace

from app.operations import inventory_codex_shadow as runner


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
        return SimpleNamespace(returncode=0, stdout=b"Logged in using ChatGPT account sensitive@example.net")

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
