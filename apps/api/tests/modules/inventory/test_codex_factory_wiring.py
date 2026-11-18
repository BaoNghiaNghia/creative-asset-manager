"""Regression: every Inventory production AI builder selects managed Codex CLI."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.modules.inventory.ai.codex_cli_gateway import RuntimeInventoryCodexGateway
from app.modules.inventory.ai.runtime_factory import build_inventory_gateway
from app.modules.inventory.daily_sheet.agent_v4.service import build_daily_sheet_v4_service
from app.modules.inventory.daily_sheet.agent.service import build_daily_sheet_agent_service
from app.modules.inventory.daily.carry_forward_planner import build_carry_forward_planner
from app.modules.inventory.daily_sheet.semantic import build_daily_sheet_semantic_analyzer


def deployment():
    return Settings(_env_file=None).model_copy(update={
        "INVENTORY_AI_ENABLED": True,
        "INVENTORY_AI_PROVIDER": "codex_cli",
        "INVENTORY_AI_ALLOWED_MODELS": "codex-cli-default",
        "INVENTORY_CODEX_ACCOUNT": "account-1",
        "INVENTORY_CODEX_TIMEOUT_SECONDS": 80,
    })


def test_production_inventory_gateways_never_require_gemini_credential(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    sessions = sessionmaker(engine)
    settings = deployment()
    gateway = build_inventory_gateway(sessions, settings)
    assert isinstance(gateway, RuntimeInventoryCodexGateway)
    assert gateway.config.account == "account-1"
    assert gateway.config.timeout_seconds == 80

    v4 = build_daily_sheet_v4_service(
        session_factory=sessions, settings=settings,
        context_provider=lambda tenant: None,
        client_factory=lambda _: None, token_resolver=lambda _: None,
    )
    assert isinstance(v4.gateway, RuntimeInventoryCodexGateway)
    assert v4.required_provider == "codex_cli"
    assert v4.deployment_allowed_models == ("codex-cli-default",)

    carry = build_carry_forward_planner(
        session_factory=sessions, settings=settings,
        client_factory=lambda _: None, token_resolver=lambda _: None,
    )
    assert isinstance(carry.gateway, RuntimeInventoryCodexGateway)
    assert carry.required_provider == "codex_cli"

    semantic = build_daily_sheet_semantic_analyzer(
        settings=settings, session_factory=sessions,
    )
    assert isinstance(semantic.gateway, RuntimeInventoryCodexGateway)
    assert semantic.required_provider == "codex_cli"

    v3 = build_daily_sheet_agent_service(
        session_factory=sessions, settings=settings,
        context_provider=lambda tenant: None,
        client_factory=lambda _: None, token_resolver=lambda _: None,
    )
    assert isinstance(v3.planner.gateway, RuntimeInventoryCodexGateway)
    assert v3.planner.required_provider == "codex_cli"
