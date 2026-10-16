"""Read-only browser-first Amazon/Etsy scan plan for CAM operators.

Exposes no execution/claim or marketplace API endpoint. This is a visibility
contract for the isolated pilot browser runner, not proof of authorization.
"""
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.branding_dna.evidence_router import shop_for_tenant

router = APIRouter(prefix="/api/v1/branding-dna", tags=["branding-dna-browser-plan"])
READ = require_permission("ai_operations.read")

ACTIONS = (
    "open_shop_in_dedicated_chromium",
    "find_visible_listing_cards",
    "visit_bounded_product_pages",
    "extract_visible_titles_and_image_references",
    "review_partial_unverified_evidence",
)


@router.get("/shops/{shop_id}/browser-plan")
def get_shop_browser_plan(
    shop_id: str,
    response: Response,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    shop = shop_for_tenant(session, principal.active_tenant_id, shop_id)
    if shop.platform not in ("etsy", "amazon"):
        raise HTTPException(409, detail="Browser pilot is scoped to Amazon and Etsy")
    required = [
        "operator_approval_for_exact_shop",
        "marketplace_terms_and_robots_policy_review",
        "dedicated_worker_browser_binary",
        "local_profile_isolation_and_browser_qa",
        "evidence_rights_review",
    ]
    if shop.platform == "etsy":
        required.append("etsy_express_written_authorization")
    else:
        required.append("amazon_agent_policy_acknowledged_and_agent_user_agent")
    response.headers["Cache-Control"] = "no-store"
    return {
        "shop_id": shop.id,
        "platform": shop.platform,
        "storefront_url": shop.storefront_url,
        "mode": "local_browser_dom_only",
        "marketplace_api_used": False,
        "external_scraping_service_used": False,
        "scanner_enabled": False,
        "dispatchable": False,
        "pilot_max_product_pages": 5,
        "steps": list(ACTIONS),
        "blockers": required,
        "result_state": "partial_unverified",
        "listing_coverage": "unknown",
        "sales_verified": False,
        "rights_verified": False,
    }
