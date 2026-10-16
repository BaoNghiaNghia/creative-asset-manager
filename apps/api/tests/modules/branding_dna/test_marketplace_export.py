"""Amazon Seller Central and Etsy Shop Manager offline export preflight."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.branding_dna.model import BrandShopModel
from app.modules.branding_dna.router import ShopCreate, add_shop
from app.modules.branding_dna.marketplace_export_adapter import (
    MarketplaceExportError, preview_amazon_inventory_report, preview_etsy_listings_csv,
)
from app.modules.branding_dna.source_preview_router import SourcePreviewInput, preview_source_export

ETSY_IMAGE = "https://i.etsystatic.com/12345678/r/il/abcd12/9876543210/il_794xN.9876543210_ab12.jpg"
ETSY = (
    "TITLE,DESCRIPTION,PRICE,CURRENCY_CODE,QUANTITY,TAGS,MATERIALS,IMAGE1,IMAGE2,SKU\n"
    f"Cap One,Hat,18.00,USD,10,hat,cotton,{ETSY_IMAGE},,CAP-RED\n"
    "Cap Two,Hat,22.00,USD,5,hat,cotton,,,CAP-BLUE\n"
)
AMAZON = (
    "item-name\tseller-sku\tasin1\tprice\tquantity\n"
    "Twill Hat\tHAT-NAVY\tB012345678\t25.00\t3\n"
    "Twill Hat\tHAT-BLACK\tB012345678\t25.00\t5\n"
    "Canvas Hat\tHAT-CANVAS\t\t20.00\t10\n"
)


def principal(tenant="tenant-a"):
    return SimpleNamespace(active_tenant_id=tenant, actor_id="seller-admin")


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[BrandShopModel.__table__])
    with Session(engine) as session:
        yield session
    engine.dispose()


def shop(db, platform="etsy", tenant="tenant-a", access="authorized", name="MyCaps"):
    url = f"https://www.etsy.com/shop/{name}" if platform == "etsy" else f"https://www.amazon.com/stores/{name}/page/123"
    return add_shop(ShopCreate(platform=platform, storefront_url=url, access_mode=access),
                    session=db, principal=principal(tenant))


def preview(db, shop_id, format, text, tenant="tenant-a"):
    response = Response()
    result = preview_source_export(
        shop_id, SourcePreviewInput(format=format, csv_text=text), response,
        session=db, principal=principal(tenant),
    )
    assert response.headers["cache-control"] == "no-store"
    return result


def test_etsy_listing_export_preserves_order_and_marks_identity_provisional(db):
    s = shop(db)
    result = preview(db, s["id"], "etsy_listings_csv", ETSY)
    assert result["row_count"] == 2
    assert result["product_count"] == 2
    assert result["issue_count"] == 0
    assert result["items"][0]["title"] == "Cap One"
    assert result["items"][0]["image_count"] == 1
    assert result["items"][0]["variant_count"] == 1
    assert result["items"][0]["identity_quality"] == "provisional_not_canonical"
    assert "listing_id_missing" in result["items"][0]["warnings"]
    assert "image_locator_missing" in result["items"][1]["warnings"]
    assert all(result[key] is False for key in (
        "rights_verified", "source_identity_verified", "sales_verified",
        "evidence_committed", "scan_enabled",
    ))
    assert result["coverage_state"] == "unknown"


def test_amazon_tsv_groups_seller_skus_per_asin_but_no_sales_claim(db):
    s = shop(db, platform="amazon", name="MyCapsAmazon")
    result = preview(db, s["id"], "amazon_inventory_report", AMAZON)
    assert result["product_count"] == 2
    assert result["row_count"] == 3
    assert result["issue_count"] == 0
    assert result["items"][0]["variant_count"] == 2
    assert result["items"][0]["listing_key"] == "amazon-asin:B012345678"
    assert result["items"][1]["identity_quality"] == "seller_sku_only_unverified"
    assert "asin_missing" in result["items"][1]["warnings"]
    assert result["items"][0]["image_count"] == 0
    assert result["sales_verified"] is False
    assert result["evidence_committed"] is False
    assert result["scan_enabled"] is False
    assert "no_sales_or_bestseller_verification" in result["blockers"]


def test_amazon_csv_header_aliases_are_supported():
    result = preview_amazon_inventory_report(
        "sku,asin,product-name,quantity\n"
        "CAP001,B012345678,Everyday Hat,5\n"
    )
    assert result["items"][0]["listing_key"] == "amazon-asin:B012345678"
    assert result["items"][0]["title"] == "Everyday Hat"


def test_etsy_listing_id_when_present_still_unverified():
    result = preview_etsy_listings_csv(
        "Title,LISTING_ID,IMAGE1,SKU\n"
        f"Hat,12345678,{ETSY_IMAGE},CAP\n"
    )
    assert result["items"][0]["identity_quality"] == "export_claim_unverified"
    assert result["items"][0]["listing_key"] == "etsy:12345678"
    assert result["source_identity_verified"] is False


@pytest.mark.parametrize("bad", [
    "http://127.0.0.1/private.jpg",
    "https://localhost/private.jpg",
    "https://i.etsystatic.com.evil.test/secret.jpg",
    "https://i.etsystatic.com@localhost/a.jpg",
    "https://i.etsystatic.com:8443/a.jpg",
    "https://i.etsystatic.com/x.jpg?token=secret",
    "https://i.etsystatic.com/x.jpg#fragment",
    "https://evil.test/x.jpg",
])
def test_etsy_rejects_unsafe_images_and_does_not_echo_locator(bad):
    result = preview_etsy_listings_csv("Title,IMAGE1\n" + "Hat," + bad + "\n")
    assert result["issue_count"] == 1
    assert result["items"][0]["image_count"] == 0
    assert bad not in str(result)


def test_wrong_tenant_shop_and_source_format_denied(db):
    etsy = shop(db)
    amazon = shop(db, platform="amazon", name="OtherAmazon")
    for fn in [
        lambda: preview(db, etsy["id"], "etsy_listings_csv", ETSY, tenant="other-tenant"),
        lambda: preview(db, amazon["id"], "amazon_inventory_report", AMAZON, tenant="other-tenant"),
    ]:
        with pytest.raises(HTTPException) as error:
            fn()
        assert error.value.status_code == 404
    for identifier, fmt, body in [
        (etsy["id"], "amazon_inventory_report", AMAZON),
        (amazon["id"], "etsy_listings_csv", ETSY),
    ]:
        with pytest.raises(HTTPException) as error:
            preview(db, identifier, fmt, body)
        assert error.value.status_code == 409
    public = shop(db, name="PublicCaps", access="public_observation")
    with pytest.raises(HTTPException) as error:
        preview(db, public["id"], "etsy_listings_csv", ETSY)
    assert error.value.status_code == 403


@pytest.mark.parametrize("format,body", [
    ("etsy_listings_csv", "price,quantity,image1\n5,1,\n"),
    ("amazon_inventory_report", "price\tquantity\n5\t10\n"),
    ("etsy_listings_csv", "Title,Title,IMAGE1\nHat,Hat,\n"),
    ("etsy_listings_csv", 'Title,IMAGE1\n"unterminated'),
    ("amazon_inventory_report", "sku\tproduct-name\n" + ("A\tHat\n" * 1001)),
])
def test_invalid_export_never_accepted(format, body):
    fn = preview_etsy_listings_csv if format == "etsy_listings_csv" else preview_amazon_inventory_report
    with pytest.raises(MarketplaceExportError):
        fn(body)


def test_reject_unsupported_format_and_injected_tenant():
    with pytest.raises(ValidationError):
        SourcePreviewInput(format="amazon_order_report", csv_text=AMAZON)
    with pytest.raises(ValidationError):
        SourcePreviewInput(format="etsy_listings_csv", csv_text=ETSY, tenant_id="tenant-b")


def test_etsy_duplicate_listing_id_is_flagged_not_merged():
    result = preview_etsy_listings_csv(
        "Title,LISTING_ID,IMAGE1\n"
        f"Hat,1234,{ETSY_IMAGE}\n"
        f"Another,1234,{ETSY_IMAGE}\n"
    )
    assert result["product_count"] == 2
    assert result["issue_count"] == 1
    assert result["issues"][0]["code"] == "etsy_listing_id_repeated"


def test_amazon_skips_invalid_sku_and_does_not_guess_identity():
    result = preview_amazon_inventory_report(
        "sku,item-name,asin\n"
        ",No SKU,B012345678\n"
        "CAP,Hat,BAD-ASIN\n"
    )
    assert result["product_count"] == 1
    assert result["issue_count"] == 2
    assert result["items"][0]["identity_quality"] == "seller_sku_only_unverified"


def test_no_export_data_is_saved_to_db(db):
    s = shop(db)
    assert preview(db, s["id"], "etsy_listings_csv", ETSY)["evidence_committed"] is False
    assert db.query(BrandShopModel).count() == 1
