from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.core.database import Base
from app.modules.branding_dna.model import BrandShopModel, BrandWorkerModel
from app.modules.branding_dna.router import (
    ShopCreate, add_shop, list_shops, list_workers, status,
    disabled_scan, normalize_shop_url, list_shops_for_paired_scout,
)
from app.modules.realistic_review_ugc.model import RrugcScoutAgentModel
from app.modules.realistic_review_ugc.service import token_digest

@pytest.fixture
def db():
    engine = create_engine('sqlite+pysqlite:///:memory:')
    Base.metadata.create_all(engine, tables=[
        BrandShopModel.__table__, BrandWorkerModel.__table__, RrugcScoutAgentModel.__table__,
    ])
    with Session(engine) as session:
        yield session
    engine.dispose()

def principal(tenant='tenant-a'):
    return SimpleNamespace(active_tenant_id=tenant, actor_id='user-a')

def shop():
    return ShopCreate(display_name='Actual Hats', platform='etsy', region='US',
        storefront_url='https://www.etsy.com/shop/ActualHats', access_mode='public_observation')

def test_tenant_registration_duplicate_and_ownership(db):
    row = add_shop(shop(), session=db, principal=principal())
    assert row['status'] == 'draft'
    assert list_shops(session=db, principal=principal())['count'] == 1
    assert list_shops(session=db, principal=principal('other'))['count'] == 0
    with pytest.raises(HTTPException) as err:
        add_shop(shop(), session=db, principal=principal())
    assert err.value.status_code == 409
    assert list_workers(session=db, principal=principal())['items'] == []
    with pytest.raises(HTTPException) as err:
        disabled_scan(row['id'], session=db, principal=principal('other'))
    assert err.value.status_code == 404

@pytest.mark.parametrize(('platform','url'), [
    ('etsy','https://www.etsy.com/listing/123456'),
    ('etsy','https://www.etsy.com.evil.com/shop/Test'),
    ('etsy','https://localhost/shop/Hat'),
    ('etsy','https://user:pass@www.etsy.com/shop/Hat'),
    ('amazon','http://www.amazon.com/stores/Hat'),
    ('amazon','https://www.amazon.com/dp/B012345678'),
    ('shopify','https://malicious.site/shop'),
    ('shopify','https://myshopify.com'),
    ('shopify','https://hat.myshopify.com:8443'),
])
def test_invalid_url_rejected(platform,url):
    with pytest.raises(ValueError):normalize_shop_url(platform,url)

def test_shop_registration_only_requires_platform_and_url(db):
    payload = ShopCreate(platform='etsy', storefront_url='https://www.etsy.com/shop/ActualHats')
    row = add_shop(payload, session=db, principal=principal())
    assert row['display_name'] == 'actualhats'
    assert row['region'] == 'US'
    assert row['access_mode'] == 'public_observation'


def test_scanner_blocked_until_worker_available(db):
    row = add_shop(shop(), session=db, principal=principal())
    assert status(session=db,principal=principal())['scanner_ready'] is False
    with pytest.raises(HTTPException) as err:
        disabled_scan(row['id'],session=db,principal=principal())
    assert err.value.status_code == 409
    assert err.value.detail['code'] == 'scanner_not_enrolled'
    assert status(session=db,principal=principal())['scan_enabled'] is False


def test_paired_scout_sees_only_own_tenant_marketplace_shops(db):
    own = add_shop(shop(), session=db, principal=principal())
    other = add_shop(ShopCreate(
        display_name="Other tenant cap", platform="amazon",
        storefront_url="https://www.amazon.com/stores/OtherCaps",
    ), session=db, principal=principal("tenant-b"))
    scout = RrugcScoutAgentModel(
        tenant_id="tenant-a", name="Windows Scout", active=True,
        token_hash=token_digest("testing-scout-token-very-long-safe-secret"),
        created_by_user_id="user-a",
    )
    db.add(scout)
    db.commit()

    for invalid in (None, "Bearer invalid"):
        with pytest.raises(HTTPException) as failure:
            list_shops_for_paired_scout(scout.id, authorization=invalid, session=db)
        assert failure.value.status_code == 401

    output = list_shops_for_paired_scout(scout.id,
        authorization="Bearer testing-scout-token-very-long-safe-secret", session=db)
    assert output["count"] == 1
    assert output["items"][0]["id"] == own["id"]
    assert output["items"][0]["id"] != other["id"]
    assert output["scan_enabled"] is False
    assert output["dispatch"] == {
        "state": "disabled",
        "reason": "scan_task_dispatch_not_deployed",
        "active_scan_jobs": 0,
        "rights_verification_required": True,
        "marketplace_authorization_verified": False,
    }

    # A valid Scout pairing stays connected even with an empty Shop Library.
    db.delete(db.get(BrandShopModel, own["id"]))
    db.commit()
    empty = list_shops_for_paired_scout(scout.id,
        authorization="Bearer testing-scout-token-very-long-safe-secret", session=db)
    assert empty["items"] == []
    assert empty["count"] == 0
    assert empty["dispatch"]["state"] == "disabled"

    scout.active = False
    db.commit()
    with pytest.raises(HTTPException) as revoked:
        list_shops_for_paired_scout(scout.id,
            authorization="Bearer testing-scout-token-very-long-safe-secret", session=db)
    assert revoked.value.status_code == 401
