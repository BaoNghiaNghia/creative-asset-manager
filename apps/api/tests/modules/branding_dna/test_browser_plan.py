"""BD-01 read-only Amazon/Etsy browser plan contract."""
from types import SimpleNamespace
import pytest
from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.core.database import Base
from app.modules.branding_dna.model import BrandShopModel
from app.modules.branding_dna.router import add_shop, ShopCreate
from app.modules.branding_dna.browser_plan_router import get_shop_browser_plan


@pytest.fixture
def db():
    engine=create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine,tables=[BrandShopModel.__table__])
    with Session(engine) as session: yield session
    engine.dispose()


def who(tenant="A"):
    return SimpleNamespace(active_tenant_id=tenant,actor_id="curator")


def save(db,platform,tenant="A"):
    url = ("https://www.etsy.com/shop/CreativeCaps" if platform=="etsy" else
           "https://www.amazon.com/stores/CreativeCaps/page/ABC123" if platform=="amazon" else
           "https://creativecaps.myshopify.com/")
    return add_shop(ShopCreate(platform=platform,storefront_url=url),session=db,principal=who(tenant))


@pytest.mark.parametrize("platform",["etsy","amazon"])
def test_browser_plan_has_no_execution_side_effects(db,platform):
    shop=save(db,platform)
    response=Response()
    plan=get_shop_browser_plan(shop["id"],response,session=db,principal=who())
    assert plan["shop_id"]==shop["id"]
    assert plan["mode"]=="local_browser_dom_only"
    assert plan["marketplace_api_used"] is False
    assert plan["external_scraping_service_used"] is False
    assert plan["scanner_enabled"] is False
    assert plan["dispatchable"] is False
    assert plan["pilot_max_product_pages"]<=5
    assert plan["rights_verified"] is False and plan["sales_verified"] is False
    assert plan["listing_coverage"]=="unknown"
    assert response.headers["Cache-Control"]=="no-store"
    assert "visit_bounded_product_pages" in plan["steps"]
    assert ("etsy_express_written_authorization" in plan["blockers"]) == (platform=="etsy")
    assert ("amazon_agent_policy_acknowledged_and_agent_user_agent" in plan["blockers"]) == (platform=="amazon")
    assert db.query(BrandShopModel).count()==1


def test_tenant_and_platform_boundaries(db):
    etsy=save(db,"etsy")
    for tenant in ["B","C"]:
        with pytest.raises(HTTPException) as err:
            get_shop_browser_plan(etsy["id"],Response(),session=db,principal=who(tenant))
        assert err.value.status_code==404
    shopify=save(db,"shopify")
    with pytest.raises(HTTPException) as err:
        get_shop_browser_plan(shopify["id"],Response(),session=db,principal=who())
    assert err.value.status_code==409
