"""G3 one-shot pilot CLI safety checks; absolutely no real provider or DB I/O."""
from __future__ import annotations

import json
from types import SimpleNamespace

from app.domain.providers.registry import AiProviderUnavailableError
from app.modules.realistic_review_ugc import typography_pilot_once as pilot
from app.modules.realistic_review_ugc.typography_visual_analyzer import AnalysisTick


def flags(*, enabled=False, pilot_enabled=False, tenant="tenant-a", rollback=True):
    return SimpleNamespace(
        TYPOGRAPHY_V2_ENABLED=enabled,
        TYPOGRAPHY_V2_PILOT_WORKER_ENABLED=pilot_enabled,
        TYPOGRAPHY_V2_PILOT_ROLLBACK_VERIFIED=rollback,
        TYPOGRAPHY_V2_CANARY_TENANT_IDS=tenant,
    )


def test_dry_run_does_not_even_construct_settings_storage_or_provider(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("dry run must never access runtime dependencies")
    monkeypatch.setattr(pilot, "get_settings", forbidden)
    monkeypatch.setattr(pilot, "build_ai_provider_registry", forbidden)
    monkeypatch.setattr(pilot, "build_managed_storage_provider", forbidden)
    assert pilot.main(["--tenant-id", "tenant-a"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "status": "dry_run_only", "provider_calls": 0, "latency_ms": 0,
    }


def test_execute_is_still_blocked_by_separate_pilot_flag(monkeypatch, capsys):
    monkeypatch.setattr(pilot, "get_settings", lambda: flags(enabled=True))
    def forbidden(*args, **kwargs):
        raise AssertionError("disabled pilot cannot construct provider/storage")
    monkeypatch.setattr(pilot, "build_ai_provider_registry", forbidden)
    monkeypatch.setattr(pilot, "build_managed_storage_provider", forbidden)
    assert pilot.main(["--tenant-id", "tenant-a", "--execute"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pilot_worker_disabled"


def test_tenant_not_allowlisted_does_not_construct_provider(monkeypatch, capsys):
    monkeypatch.setattr(pilot, "get_settings", lambda: flags(
        enabled=True, pilot_enabled=True, tenant="tenant-b",
    ))
    def forbidden(*args, **kwargs):
        raise AssertionError("wrong tenant must not construct provider")
    monkeypatch.setattr(pilot, "build_ai_provider_registry", forbidden)
    monkeypatch.setattr(pilot, "build_managed_storage_provider", forbidden)
    assert pilot.main(["--tenant-id", "tenant-a", "--execute"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "tenant_not_enabled"


def test_unverified_production_rollback_blocks_pilot_before_provider_construction(monkeypatch, capsys):
    monkeypatch.setattr(pilot, "get_settings", lambda: flags(
        enabled=True, pilot_enabled=True, rollback=False,
    ))
    def forbidden(*args, **kwargs):
        raise AssertionError("unverified rollback must never construct provider")
    monkeypatch.setattr(pilot, "build_ai_provider_registry", forbidden)
    monkeypatch.setattr(pilot, "build_managed_storage_provider", forbidden)
    assert pilot.main(["--tenant-id", "tenant-a", "--execute"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "rollback_gate_unverified"


class FakeRegistry:
    def __init__(self, *, available=True):
        self.available = available
        self.closed = False
        self.providers = []

    def require(self, name):
        self.providers.append(name)
        if not self.available:
            raise AiProviderUnavailableError(name)
        return SimpleNamespace(provider_name="gemini")

    async def aclose(self):
        self.closed = True


def test_explicit_approved_tick_runs_once_and_closes_registry(monkeypatch, capsys):
    monkeypatch.setattr(pilot, "get_settings", lambda: flags(enabled=True, pilot_enabled=True))
    monkeypatch.setattr(pilot, "tenant_can_analyze", lambda tenant: tenant == "tenant-a")
    monkeypatch.setattr(pilot, "build_managed_storage_provider", lambda settings: object())
    registry = FakeRegistry()
    monkeypatch.setattr(pilot, "build_ai_provider_registry", lambda settings, session_factory: registry)
    observed = []
    class FakeRunner:
        def __init__(self, session_factory, storage, provider):
            assert provider.provider_name == "gemini"
        async def tick(self, *, tenant_id, worker_id, concurrency):
            observed.append((tenant_id, worker_id, concurrency))
            return AnalysisTick("completed", provider_calls=1, latency_ms=100)
    monkeypatch.setattr(pilot, "TypographyVisualAnalysisRunner", FakeRunner)
    assert pilot.main(["--tenant-id", "tenant-a", "--execute"]) == 0
    assert len(observed) == 1
    assert observed[0][0] == "tenant-a" and observed[0][2] == 1
    assert registry.providers == ["gemini"] and registry.closed
    assert json.loads(capsys.readouterr().out) == {
        "status": "completed", "provider_calls": 1, "latency_ms": 100,
    }


def test_missing_configured_gemini_is_fail_closed_and_closes_registry(monkeypatch, capsys):
    monkeypatch.setattr(pilot, "get_settings", lambda: flags(enabled=True, pilot_enabled=True))
    monkeypatch.setattr(pilot, "tenant_can_analyze", lambda _tenant: True)
    monkeypatch.setattr(pilot, "build_managed_storage_provider", lambda settings: object())
    registry = FakeRegistry(available=False)
    monkeypatch.setattr(pilot, "build_ai_provider_registry", lambda settings, session_factory: registry)
    assert pilot.main(["--tenant-id", "tenant-a", "--execute"]) == 2
    assert registry.closed
    assert json.loads(capsys.readouterr().out)["status"] == "vision_provider_unavailable"
