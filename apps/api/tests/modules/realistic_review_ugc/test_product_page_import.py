from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.realistic_review_ugc.model import RrugcProductModel
from app.modules.realistic_review_ugc.product_page_import import (
    ProductPageData,
    ProductPageImportError,
    generated_sku,
    infer_product_type,
    parse_product_html,
    validate_public_https_url,
)
from app.modules.realistic_review_ugc.product_registry import RrugcProductRegistry


PRODUCT_HTML = """
<!doctype html>
<html>
<head>
  <title>Fallback product title</title>
  <link rel="canonical" href="https://shop.example.com/products/forest-cap" />
  <meta property="og:image" content="https://cdn.example.com/og.jpg" />
  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@type": "Product",
    "name": "Forest Green Baseball Cap",
    "sku": "CAP-001",
    "brand": {"@type": "Brand", "name": "North Studio"},
    "description": "Cotton twill baseball cap for casual outdoor wear.",
    "category": "Baseball Caps",
    "color": "Forest Green",
    "material": "Cotton Twill",
    "image": [
      "https://cdn.example.com/front.jpg",
      "https://cdn.example.com/side.jpg"
    ],
    "offers": {
      "@type": "Offer",
      "price": "24.95",
      "priceCurrency": "USD"
    },
    "hasVariant": [
      {"@type": "Product", "sku": "CAP-001-S", "name": "Small", "size": "S"},
      {"@type": "Product", "sku": "CAP-001-L", "name": "Large", "size": "L"}
    ]
  }
  </script>
</head>
<body><img src="/fallback.jpg" /></body>
</html>
"""


def test_parse_product_page_prefers_structured_product_data():
    result = parse_product_html(
        PRODUCT_HTML,
        "https://shop.example.com/products/forest-cap?utm_source=test",
    )

    assert result.name == "Forest Green Baseball Cap"
    assert result.sku == "CAP-001"
    assert result.brand == "North Studio"
    assert result.category == "Baseball Caps"
    assert result.color == "Forest Green"
    assert result.material == "Cotton Twill"
    assert result.price_text == "24.95"
    assert result.currency == "USD"
    assert result.source_url == "https://shop.example.com/products/forest-cap"
    assert result.images[:2] == [
        "https://cdn.example.com/front.jpg",
        "https://cdn.example.com/side.jpg",
    ]
    assert len(result.variants) == 2


def test_parse_product_page_falls_back_to_open_graph():
    result = parse_product_html(
        """
        <html><head>
          <meta property="og:title" content="Cream Embroidered Tote" />
          <meta property="og:description" content="Personalized canvas tote bag" />
          <meta property="og:image" content="/images/tote.jpg" />
        </head></html>
        """,
        "https://store.example.com/products/tote",
    )
    assert result.name == "Cream Embroidered Tote"
    assert result.description == "Personalized canvas tote bag"
    assert result.images == ["https://store.example.com/images/tote.jpg"]


def test_generated_sku_is_stable_and_product_type_uses_page_details():
    url = "https://store.example.com/products/forest-cap"
    assert generated_sku(url) == generated_sku(url)
    assert generated_sku(url).startswith("STORE-")
    assert infer_product_type("Forest Green Baseball Cap", None, None) == "hat"
    assert infer_product_type("Personalized Canvas Tote", None, None) == "tote"


def test_public_url_validator_rejects_non_https_and_loopback():
    with pytest.raises(ProductPageImportError):
        asyncio.run(validate_public_https_url("http://shop.example.com/product/1"))
    with pytest.raises(ProductPageImportError):
        asyncio.run(validate_public_https_url("https://127.0.0.1/product/1"))


def test_imported_product_upsert_reuses_source_url_and_refreshes_metadata():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    RrugcProductModel.__table__.create(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    try:
        with factory() as session:
            registry = RrugcProductRegistry(session)
            first, created = registry.upsert_imported_product(
                tenant_id="tenant-a",
                user_id="user-a",
                data=ProductPageData(
                    source_url="https://shop.example.com/products/cap",
                    source_host="shop.example.com",
                    name="Green Baseball Cap",
                    sku="CAP-1",
                    brand="Studio",
                    description="Cotton cap",
                    category="Baseball Caps",
                    color="Green",
                    material="Cotton",
                    images=["https://cdn.example.com/front.jpg"],
                ),
            )
            assert created is True
            assert first.product_type == "hat"
            assert first.source_images_json == ["https://cdn.example.com/front.jpg"]

            second, created_again = registry.upsert_imported_product(
                tenant_id="tenant-a",
                user_id="user-a",
                data=ProductPageData(
                    source_url="https://shop.example.com/products/cap",
                    source_host="shop.example.com",
                    name="Green Baseball Cap v2",
                    sku="CAP-1",
                    brand="Studio",
                    description="Updated cotton cap",
                    category="Baseball Caps",
                    color="Forest Green",
                    material="Cotton Twill",
                    images=["https://cdn.example.com/front-v2.jpg"],
                ),
            )
            assert created_again is False
            assert second.id == first.id
            assert second.name == "Green Baseball Cap v2"
            assert second.color == "Forest Green"
            assert second.revision == 2

            third, created_third = registry.upsert_imported_product(
                tenant_id="tenant-a",
                user_id="user-a",
                data=ProductPageData(
                    source_url="https://shop.example.com/products/cap",
                    source_host="shop.example.com",
                    name="Green Baseball Cap v2",
                    sku="CAP-1",
                    brand="Studio",
                    description="Updated cotton cap",
                    category="Baseball Caps",
                    color="Forest Green",
                    material="Cotton Twill",
                    images=[],
                ),
            )
            assert created_third is False
            assert third.revision == 2
            assert third.source_images_json == ["https://cdn.example.com/front-v2.jpg"]
            assert third.source_fetched_at is not None
    finally:
        engine.dispose()
