"""Immutable Ecommerce 1-4 asset requirements for the future durable runner.

Do not confuse a browser compositing URL or a stock reference with a saved
asset. No job is eligible until each role has a persisted, tenant-owned asset.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

@dataclass(frozen=True)
class EcommerceShot:
    stage: str
    view: str
    required_roles: tuple[str, ...]
    embroidery_surface: str | None

SHOTS = (
    EcommerceShot("prod_s2_ec1", "front", ("design", "stock_front"), "front"),
    EcommerceShot("prod_s2_ec2", "three_quarter", ("design", "embroidered_front", "stock_front", "stock_side_90"), "front"),
    EcommerceShot("prod_s2_ec3", "side_90", ("design", "embroidered_front", "stock_side_90"), "front"),
    EcommerceShot("prod_s2_ec4", "back", ("stock_back",), None),
)
SHOTS_BY_STAGE = {shot.stage: shot for shot in SHOTS}

def missing_persisted_inputs(stage: str, assets: Mapping[str, str | None]) -> tuple[str, ...]:
    """Return all missing durable file IDs (never accept transient blob/http URLs)."""
    if stage not in SHOTS_BY_STAGE:
        raise ValueError("Unknown Ecommerce skill stage")
    required = SHOTS_BY_STAGE[stage].required_roles
    return tuple(role for role in required if not _persisted_asset_id(assets.get(role)))

def _persisted_asset_id(value: object) -> bool:
    if not isinstance(value, str) or not value or len(value) > 255:
        return False
    if value.startswith(("blob:", "data:", "http:", "https:", "/")):
        return False
    return all(c.isascii() and (c.isalnum() or c in "-_:") for c in value)

def build_execution_preflight(stage: str, assets: Mapping[str, str | None], *, worker_ready: bool, skill_ready: bool) -> dict:
    """Fail closed, including where the selected Skill is missing or the worker is gated."""
    missing = missing_persisted_inputs(stage, assets)
    return {
        "stage": stage,
        "view": SHOTS_BY_STAGE[stage].view,
        "required_roles": SHOTS_BY_STAGE[stage].required_roles,
        "missing_roles": missing,
        "ready": not missing and worker_ready and skill_ready,
        "status": "missing_inputs" if missing else "missing_skill" if not skill_ready else "worker_pending" if not worker_ready else "ready",
    }
