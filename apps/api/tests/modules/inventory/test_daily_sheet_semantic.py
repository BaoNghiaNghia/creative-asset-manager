from __future__ import annotations

from pathlib import Path
import tempfile

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.core.database import Base
from app.modules.auth_persistence.model import TenantModel
from app.modules.inventory.ai.gateway import InventoryAiGatewayResult
from app.modules.inventory.daily_sheet import semantic
from app.modules.inventory.model import InventoryAiControlModel


def test_production_semantic_builder_invokes_inventory_gemini_boundary(monkeypatch):
    temporary = tempfile.TemporaryDirectory()
    engine = create_engine(f"sqlite:///{Path(temporary.name) / 'semantic.db'}")
    Base.metadata.tables["tenants"].create(engine)
    Base.metadata.tables["inventory_ai_controls"].create(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions.begin() as session:
        session.add(TenantModel(id="tenant-a", name="Tenant A", slug="tenant-a"))
        session.add(InventoryAiControlModel(
            tenant_id="tenant-a", enabled=True, emergency_stop=False,
            provider="gemini", allowed_models_json=["gemini-test"],
        ))

    calls = []
    class FakeGateway:
        def __init__(self, resolver, **kwargs):
            self.resolver = resolver
        def analyze_structured_text(self, **kwargs):
            calls.append(kwargs)
            return InventoryAiGatewayResult(
                raw_response_json={},
                extracted_json={
                    "status": "parsed", "raw": "about ten", "canonical_value": "10",
                    "canonical_unit": "count", "confidence": 0.99,
                    "requires_review": False, "warnings": [],
                },
            )

    monkeypatch.setattr(semantic, "RuntimeInventoryGeminiGateway", FakeGateway)
    analyzer = semantic.build_daily_sheet_semantic_analyzer(
        Settings(INVENTORY_AI_ENABLED=True, INVENTORY_TENANT_ALLOWLIST="tenant-a"), session_factory=sessions
    )
    result = analyzer.analyze_quantity("tenant-a", {
        "raw": "about ten", "cell": "H2", "item_name": "Material",
        "category": "Category", "nearby_business_cells": [],
        "approved_package_conversions": {},
    })
    assert result["canonical_value"] == "10"
    assert calls[0]["tenant_id"] == "tenant-a"
    assert calls[0]["provider"] == "gemini"
    assert calls[0]["model"] == "gemini-test"
    assert "about ten" in calls[0]["prompt"]
    engine.dispose()
    temporary.cleanup()


def test_router_normal_service_factory_invokes_inventory_gemini_boundary(monkeypatch):
    from app.modules.inventory.daily_sheet import router

    temporary = tempfile.TemporaryDirectory()
    engine = create_engine(f"sqlite:///{Path(temporary.name) / 'service-semantic.db'}")
    Base.metadata.tables["tenants"].create(engine)
    Base.metadata.tables["inventory_ai_controls"].create(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions.begin() as session:
        session.add(TenantModel(id="tenant-a", name="Tenant A", slug="tenant-a"))
        session.add(InventoryAiControlModel(
            tenant_id="tenant-a", enabled=True, emergency_stop=False,
            provider="gemini", allowed_models_json=["gemini-production-factory"],
        ))

    calls = []

    class FakeGateway:
        def __init__(self, resolver, **kwargs):
            self.resolver = resolver

        def analyze_structured_text(self, **kwargs):
            calls.append(kwargs)
            return InventoryAiGatewayResult(
                raw_response_json={},
                extracted_json={
                    "status": "parsed",
                    "raw": "about ten",
                    "canonical_value": "10",
                    "canonical_unit": "count",
                    "confidence": 0.99,
                    "requires_review": False,
                    "warnings": [],
                },
            )

    settings = Settings(
        INVENTORY_AI_ENABLED=True,
        INVENTORY_TENANT_ALLOWLIST="tenant-a",
    )
    monkeypatch.setattr(semantic, "RuntimeInventoryGeminiGateway", FakeGateway)
    monkeypatch.setattr(semantic, "get_settings", lambda: settings)
    monkeypatch.setattr(router, "SessionLocal", sessions)

    worker = router._service()
    result = worker.semantic_analyzer.analyze_quantity(
        "tenant-a",
        {
            "raw": "about ten",
            "cell": "H2",
            "item_name": "Material",
            "category": "Category",
            "nearby_business_cells": [],
            "approved_package_conversions": {},
        },
    )

    assert result["canonical_value"] == "10"
    assert calls[0]["tenant_id"] == "tenant-a"
    assert calls[0]["model"] == "gemini-production-factory"
    engine.dispose()
    temporary.cleanup()
