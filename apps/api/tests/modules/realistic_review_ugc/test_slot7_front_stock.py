import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from app.modules.realistic_review_ugc import slot7_front_stock as stock


def jpeg(color):
    image = Image.new("RGB", (1200, 1200), color)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=88)
    return buffer.getvalue()


class FakeResponse(io.BytesIO):
    def __init__(self, url, content):
        super().__init__(content)
        self.url = url
    def geturl(self):
        return self.url


def test_exact_supplier_variants_and_images_validate_before_import(monkeypatch):
    images = {}
    variants = []
    for i, (color, (title, slug)) in enumerate(stock.SUPPLIER_VARIANTS.items()):
        url = ("https://cdn.shopify.com/s/files/1/1567/7353/files/"
               f"8869_{i}_Valucap_8869_{slug}_Front_High.jpg?v=1")
        images[url] = jpeg(["#7b264e", "#111111", "#172342", "#cb1e26"][i])
        variants.append({"id": i + 1, "title": title, "featured_image": {"src": url}})
    catalog = {"id": "sample-product", "title": "Valucap - Five-Panel Twill Cap - 8869",
               "variants": variants}
    def urlopen(req, timeout):
        url = req.full_url
        if url == stock.PRODUCT_JSON:
            return FakeResponse(url, json.dumps(catalog).encode())
        return FakeResponse(url, images[url])
    monkeypatch.setattr(stock.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(stock, "EXPECTED_SHA256", {
        color: hashlib.sha256(images[variant["featured_image"]["src"]]).hexdigest()
        for (color, _), variant in zip(stock.SUPPLIER_VARIANTS.items(), variants)
    })
    four = stock.fetch_verified_supplier_fronts()
    assert len(four) == 4
    assert all(r["width"] == 1200 and r["height"] == 1200 for r in four)
    assert {item["color"] for item in four} == set(stock.SUPPLIER_VARIANTS)

    catalog["variants"][0]["featured_image"]["src"] = (
        "https://cdn.shopify.com.evil.test/mismatched.jpg")
    with pytest.raises(stock.Slot7StockError, match="untrusted"):
        stock.fetch_verified_supplier_fronts()
    catalog["variants"][0]["featured_image"]["src"] = variants[0]["featured_image"]["src"] = (
        "https://cdn.shopify.com/s/files/1/1567/7353/files/"
        "8869_0_Valucap_8869_Khaki-_Maroon_Front_High.jpg?v=1"
    )
    images[variants[0]["featured_image"]["src"]] = jpeg("#0000ff")
    with pytest.raises(stock.Slot7StockError, match="Supplier image changed"):
        stock.fetch_verified_supplier_fronts()


def test_supplier_fronts_are_independent_from_three_angle_stock(monkeypatch, tmp_path):
    data = [{
        "color": color, "source_variant": title,
        "supplier_product_id": "product", "supplier_variant_id": str(i),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw), "width": 1200, "height": 1200,
        "url": (f"https://cdn.shopify.com/s/files/1/1567/7353/files/"
                f"Valucap_8869_{slug}_Front_High.jpg"),
        "data": raw,
    } for i, (color, (title, slug)) in enumerate(stock.SUPPLIER_VARIANTS.items())
      for raw in [jpeg(("#eeddcc", "#222222", "#334455", "#d01e25")[i])]]
    monkeypatch.setattr(stock, "fetch_verified_supplier_fronts", lambda: data)
    settings = SimpleNamespace(CODEX_IMAGE_HOME=str(tmp_path))
    root = stock.slot7_root(settings)
    preview = stock.sync_supplier_fronts(settings)
    assert not preview["applied"] and len(preview["changes"]) == 4
    assert not root.exists()
    assert stock.sync_supplier_fronts(settings, apply=True)["applied"]
    assert root.joinpath("manifest.json").is_file()
    assert len(list(root.glob("sets/*/*/front.jpg"))) == 4
    for item in data:
        color = item["color"]
        assert stock.configured_slot7_front(settings, color)
        file, digest = stock.read_slot7_front(settings, color)
        assert file.read_bytes() == item["data"]
        assert digest == item["sha256"]
    assert stock.sync_supplier_fronts(settings, apply=True)["changes"] == []
    assert stock.read_slot7_front(settings, "natural-brown") is None

    file, _ = stock.read_slot7_front(settings, data[0]["color"])
    file.write_bytes(b"altered")
    assert stock.read_slot7_front(settings, data[0]["color"]) is None
    assert stock.configured_slot7_front(settings, data[0]["color"])


def test_stock_path_prioritizes_full_managed_stock_over_supplier_front(monkeypatch, tmp_path):
    from app.modules.realistic_review_ugc import colorways, hat_stock_sync
    settings = SimpleNamespace(CODEX_IMAGE_HOME=str(tmp_path))
    full = tmp_path / "full.jpg"
    full.write_bytes(b"managed")
    front = tmp_path / "slot7.jpg"
    front.write_bytes(b"supplier")
    monkeypatch.setattr(hat_stock_sync, "read_managed_stock",
                        lambda s, color, view: (full, "managed") if color == "natural-red" else None)
    monkeypatch.setattr(stock, "read_slot7_front",
                        lambda s, color: (front, "verified") if color in stock.SUPPLIER_VARIANTS else None)
    monkeypatch.setattr(stock, "configured_slot7_front",
                        lambda s, color: color in stock.SUPPLIER_VARIANTS)
    assert colorways._stock_path(settings, "natural-red") == full
    assert colorways._stock_path(settings, "natural-navy") == front
    assert colorways._stock_path(settings, "natural-brown") != front

    monkeypatch.setattr(stock, "read_slot7_front", lambda *args: None)
    with pytest.raises(colorways.ColorwayError, match="missing or corrupt"):
        colorways._stock_path(settings, "natural-navy")
