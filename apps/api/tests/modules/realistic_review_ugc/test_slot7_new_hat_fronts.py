import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.modules.realistic_review_ugc import slot7_front_stock as supplier
from app.modules.realistic_review_ugc import slot7_new_hat_fronts as new_hat


def source_photo(bill: str) -> bytes:
    image = Image.new("RGB", (1254, 1254), "#ffffff")
    brush = ImageDraw.Draw(image)
    brush.ellipse((130, 190, 1120, 900), fill=(237, 236, 238))
    brush.rounded_rectangle((120, 685, 1130, 1015), radius=60, fill=bill)
    brush.ellipse((602, 145, 652, 191), fill=bill)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_recolor_preserves_background_bill_and_dark_button():
    raw = source_photo("#0d1730")
    new = new_hat.render_natural_front(raw)
    assert new != raw
    assert len(new) > 10000
    with Image.open(io.BytesIO(new)) as result:
        assert result.size == (1254, 1254)
        background = result.getpixel((30, 30))
        crown = result.getpixel((620, 480))
        bill = result.getpixel((620, 930))
        button = result.getpixel((620, 165))
        assert all(channel >= 250 for channel in background)
        assert crown[0] > crown[1] > crown[2] + 10
        assert bill[2] >= bill[0] and bill[0] < 70
        assert button[0] < 100


def test_preview_read_only_atomic_import_and_supplier_protection(monkeypatch, tmp_path):
    inputs = {
        "natural-brown": source_photo("#663c23"),
        "white-black": source_photo("#111111"),
        "white-navy": source_photo("#0c1836"),
    }
    expected = {
        color: hashlib.sha256(raw).hexdigest()
        for color, raw in inputs.items()
    }
    monkeypatch.setattr(new_hat, "PINNED_INPUTS", expected)
    for color, raw in inputs.items():
        (tmp_path / (color + ".png")).write_bytes(raw)

    monkeypatch.setattr(new_hat, "read_managed_stock", lambda settings, color, view:
                        (tmp_path / (color + ".png"), "irrelevant"))
    settings = SimpleNamespace(CODEX_IMAGE_HOME=str(tmp_path))
    root = supplier.slot7_root(settings)
    before = new_hat.reconcile_new_hat_fronts(settings)
    assert before["changes"] == ["natural-black", "natural-navy"]
    assert not before["applied"]
    assert not root.exists()

    applied = new_hat.reconcile_new_hat_fronts(settings, apply=True)
    assert applied["applied"] and len(applied["changes"]) == 2
    assert set(supplier._manifest(root)["colors"]) == {"natural-black", "natural-navy"}
    for color in new_hat.DERIVED_TARGETS:
        current = supplier.read_slot7_front(settings, color)
        assert current is not None
        assert hashlib.sha256(current[0].read_bytes()).hexdigest() == current[1]
        assert supplier._manifest(root)["colors"][color]["origin"] == new_hat.ORIGIN
    second = new_hat.reconcile_new_hat_fronts(settings)
    assert second["changes"] == []

    external = [{
        "color": color,
        "source_variant": color,
        "supplier_product_id": "x",
        "supplier_variant_id": "x",
        "sha256": hashlib.sha256(photo).hexdigest(),
        "bytes": len(photo), "width": 1254, "height": 1254,
        "url": "https://cdn.shopify.com/s/files/1/1567/7353/files/old.jpg",
        "data": photo,
    } for color, photo in (
        ("natural-black", source_photo("#000000")),
        ("natural-navy", source_photo("#000040")),
    )]
    monkeypatch.setattr(supplier, "fetch_verified_supplier_fronts",
                        lambda: external)
    result = supplier.sync_supplier_fronts(settings, apply=True)
    assert result["changes"] == []
    assert set(result["protected_derived_colorways"]) == set(new_hat.DERIVED_TARGETS)
    for color in new_hat.DERIVED_TARGETS:
        assert supplier.read_slot7_front(settings, color)[1] == applied["images"][color]["sha256"]


def test_changed_input_is_blocked_before_stock_write(monkeypatch, tmp_path):
    monkeypatch.setattr(new_hat, "read_managed_stock", lambda *a:
                        (tmp_path / "unexpected.png", "bad"))
    (tmp_path / "unexpected.png").write_bytes(source_photo("#000000"))
    settings = SimpleNamespace(CODEX_IMAGE_HOME=str(tmp_path))
    with pytest.raises(new_hat.DerivedFrontError, match="reference changed"):
        new_hat.reconcile_new_hat_fronts(settings, apply=True)
    assert not supplier.slot7_root(settings).exists()
