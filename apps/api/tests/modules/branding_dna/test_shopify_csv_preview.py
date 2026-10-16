"""Offline source preflight: no arbitrary web fetch or falsely verified evidence."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.branding_dna.model import BrandShopModel
from app.modules.branding_dna.router import ShopCreate, add_shop
from app.modules.branding_dna.shopify_csv_adapter import (
    MAX_CSV_ROWS, ShopifyCsvError, preview_shopify_csv,
)
from app.modules.branding_dna.source_preview_router import SourcePreviewInput, preview_source_export


HEADERS = "Handle,Title,Variant SKU,Image Src,Image Position,Type\n"
A = "https://cdn.shopify.com/s/files/1/1234/5678/products/front.jpg?v=123"
B = "https://cdn.shopify.com/s/files/1/1234/5678/products/side.jpg?v=124"
CSV = (HEADERS +
       f"tw-cap,Cap Twill,SKU-NAVY,{A},1,Hats\n" +
       f"tw-cap,,SKU-BLACK,{B},2,\n" +
       f"cap-two,Another Cap,S2,,,\n")


def who(tenant="tenant-a"):
    return SimpleNamespace(active_tenant_id=tenant, actor_id="owner-1")


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[BrandShopModel.__table__])
    with Session(engine) as session:
        yield session
    engine.dispose()


def shop(db, platform="shopify", access="authorized", tenant="tenant-a", name="hatsbrand"):
    url = f"https://{name}.myshopify.com/" if platform == "shopify" else f"https://www.etsy.com/shop/{name}"
    return add_shop(ShopCreate(platform=platform, storefront_url=url, access_mode=access),
                    session=db, principal=who(tenant))


def invoke(db, shop_id, tenant="tenant-a", body=CSV):
    response = Response()
    result = preview_source_export(
        shop_id, SourcePreviewInput(format="shopify_products_csv", csv_text=body),
        response, session=db, principal=who(tenant))
    assert response.headers["cache-control"] == "no-store"
    return result


def test_shopify_csv_preview_uses_actual_gallery_order_without_storing_evidence(db):
    s = shop(db)
    result = invoke(db, s["id"])
    assert result["product_count"] == 2
    assert result["row_count"] == 3
    assert result["issue_count"] == 0
    item = result["items"][0]
    assert item["listing_key"] == "tw-cap" and item["title"] == "Cap Twill"
    assert item["variant_count"] == 2 and item["image_count"] == 2
    assert len(item["source_url_sha256"]) == 2
    assert all(len(x) == 64 for x in item["source_url_sha256"])
    assert result["source_identity_verified"] is False
    assert result["rights_verified"] is False
    assert result["evidence_committed"] is False
    assert result["scan_enabled"] is False
    assert "merchant_export_origin_not_verified" in result["blockers"]
    assert result["preview_state"] == "imported_export_unverified"
    assert item["category"] == "Hats"
    assert "image_locator_missing" in result["items"][1]["warnings"]
    assert "sku_missing" not in result["items"][1]["warnings"]
    assert db.query(BrandShopModel).count() == 1


def test_refuses_wrong_tenant_access_and_unsupported_platform(db):
    real_shop = shop(db, name="mybrand")
    for tenant in ["tenant-b", "tenant-other"]:
        with pytest.raises(HTTPException) as err:
            invoke(db, real_shop["id"], tenant)
        assert err.value.status_code == 404
    public_shop = shop(db, name="publicbrand", access="public_observation")
    with pytest.raises(HTTPException) as err:
        invoke(db, public_shop["id"])
    assert err.value.status_code == 403
    etsy_shop = shop(db, name="EtsyBrand", platform="etsy")
    with pytest.raises(HTTPException) as err:
        invoke(db, etsy_shop["id"])
    assert err.value.status_code == 409


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000/admin",
    "https://localhost/s/files/1/abc.jpg",
    "https://cdn.shopify.com.evil.example/s/files/1/a.jpg",
    "https://cdn.shopify.com@localhost/s/files/1/a.jpg",
    "https://cdn.shopify.com:8443/s/files/1/a.jpg",
    "https://cdn.shopify.com/s/files/1/a.jpg?token=private",
    "https://cdn.shopify.com/s/files/1/a.jpg#credentials",
    "https://evil.example/s/files/1/a.jpg",
])
def test_unsafe_media_locators_never_appear_in_preview(url):
    csv_value = HEADERS + f"safe-cap,Title,SKU,{url},1,Hats\n"
    result = preview_shopify_csv(csv_value)
    assert result["issue_count"] == 1
    assert result["items"][0]["image_count"] == 0
    assert result["items"][0]["source_url_sha256"] == []
    assert url not in str(result)


def test_csv_duplicate_headers_malformed_missing_columns_rejected():
    for value in [
        "Handle,Title,Handle,Variant SKU,Image Src,Image Position\n",
        "Title,Variant SKU,Image Src,Image Position\n",
        'Handle,Title,Variant SKU,Image Src,Image Position\n"unterminated',
        HEADERS + ("a" * 2_000_001),
    ]:
        with pytest.raises(ShopifyCsvError):
            preview_shopify_csv(value)


def test_gallery_conflict_and_missing_positions_are_reported():
    value = (
        HEADERS +
        f"cap,Title,S1,{A},1,Hats\n" +
        f"cap,,S2,{B},1,\n" +
        f"cap,,,{B},3,\n"
    )
    result = preview_shopify_csv(value)
    assert result["issue_count"] == 1
    assert result["issues"][0]["code"] == "image_position_conflict"
    assert "gallery_position_gaps" in result["items"][0]["warnings"]


def test_unverified_csv_never_reports_import_ready_even_if_clean():
    result = preview_shopify_csv(HEADERS + f"hat,Verified Looking Hat,S1,{A},1,Hats\n")
    assert result["issue_count"] == 0
    assert result["rights_verified"] is False
    assert result["evidence_committed"] is False
    assert result["scan_enabled"] is False
    assert "source_rights_not_verified" in result["blockers"]


def test_csv_hard_row_limit_and_api_extra_fields():
    value = HEADERS + "hat,Title,SKU,,,\n" * (MAX_CSV_ROWS + 1)
    with pytest.raises(ShopifyCsvError, match="csv_too_many_rows"):
        preview_shopify_csv(value)
    with pytest.raises(ValidationError):
        SourcePreviewInput(format="shopify_products_csv", csv_text=CSV,
                           tenant_id="tenant-b")
    with pytest.raises(ValidationError):
        SourcePreviewInput(format="amazon_products_csv", csv_text=CSV)


def test_bad_csv_reports_validation_code_without_leaking_source(db):
    s = shop(db)
    with pytest.raises(HTTPException) as err:
        invoke(db, s["id"], body="not,a,shopify,export")
    assert err.value.status_code == 422
    assert err.value.detail == "csv_invalid_shopify_headers"
