"""Single explicit Inventory AI provider factory.

Production may select Codex CLI using the managed ChatGPT login.
No automatic fallback to Gemini on failed Codex inference, quota or login.
The legacy Gemini branch remains only for controlled rollback/test settings.
"""
from __future__ import annotations

from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.modules.inventory.ai.gateway import RuntimeInventoryGeminiGateway
from app.modules.inventory.ai.codex_cli_gateway import build_codex_inventory_gateway
from app.modules.inventory.credentials import InventoryGeminiCredentialResolver


def build_inventory_gateway(sessions: sessionmaker, settings: Settings):
    if settings.INVENTORY_AI_PROVIDER == "codex_cli":
        return build_codex_inventory_gateway(settings)
    if settings.INVENTORY_AI_PROVIDER == "gemini":
        return RuntimeInventoryGeminiGateway(
            InventoryGeminiCredentialResolver(sessions, settings),
            timeout_seconds=settings.INVENTORY_AI_TIMEOUT_SECONDS,
        )
    raise ValueError("inventory_ai_provider_unsupported")
