from io import BytesIO

from PIL import Image
import pytest

from app.modules.realistic_review_ugc.ecommerce_stock_evidence import (
    read_release_stock, release_stock_evidence,
)


def _stock(root, filename, *, valid=True):
    path = root / "apps/client/public/rrugc/blueprint/stock/natural-black" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    if valid:
        image = Image.new("RGB", (400, 400), (240, 240, 240))
        buffer = BytesIO()
        image.save(buffer, format="JPEG")
        path.write_bytes(buffer.getvalue())
    else:
        path.write_bytes(b"not an image")
    return path


def test_official_reference_evidence_is_content_addressed(tmp_path):
    _stock(tmp_path, "front.jpg")
    _, info = read_release_stock("stock_front", project_root=tmp_path)
    assert info["ready"] if "ready" in info else info["size_bytes"] > 0
    assert len(info["sha256"]) == 64
    assert (info["width"], info["height"]) == (400, 400)


def test_unknown_or_missing_view_is_fail_closed(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_release_stock("stock_three_quarter", project_root=tmp_path)
    assert release_stock_evidence(project_root=tmp_path)["stock_three_quarter"]["ready"] is False
    assert release_stock_evidence(project_root=tmp_path)["stock_front"]["ready"] is False


def test_managed_three_quarter_is_real_verified_stock_and_never_front_or_side(tmp_path, monkeypatch):
    from app.modules.realistic_review_ugc import hat_stock_sync
    official_34 = _stock(tmp_path, "three-quarter.jpg")
    def only_three_quarter(_settings, color, view):
        return (official_34, "revision") if color == "natural-charcoal" and view == "three-quarter" else None
    monkeypatch.setattr(hat_stock_sync, "read_managed_stock", only_three_quarter)
    content, evidence = read_release_stock(
        "stock_three_quarter", project_root=tmp_path, color_key="natural-charcoal")
    assert content == official_34.read_bytes()
    assert evidence["role"] == "stock_three_quarter"
    assert len(evidence["sha256"]) == 64
    proof = release_stock_evidence(project_root=tmp_path, color_key="natural-charcoal")
    assert proof["stock_three_quarter"]["ready"] is True
    assert proof["stock_front"]["ready"] is False
    assert proof["stock_side_90"]["ready"] is False


def test_slot3_stock_is_distinct_and_prefers_verified_side_front(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.modules.realistic_review_ugc import ecommerce_pipeline, hat_stock_sync
    settings = SimpleNamespace(CODEX_IMAGE_HOME=str(tmp_path))
    monkeypatch.setattr(ecommerce_pipeline, "read_release_stock",
                        lambda role, **kwargs: (role.encode(), {"role": role}))
    monkeypatch.setattr(hat_stock_sync, "read_managed_stock",
                        lambda *_args: None)
    three_quarter, digest2 = ecommerce_pipeline._stock_refs(
        settings, "prod_s2_ec2", "natural-charcoal")
    actual_side, digest3 = ecommerce_pipeline._stock_refs(
        settings, "prod_s2_ec3", "natural-charcoal")
    assert three_quarter[0][0] == "stock_three_quarter"
    assert actual_side[0][0] == "stock_side_90"
    assert digest2 != digest3

    monkeypatch.setattr(hat_stock_sync, "read_managed_stock",
                        lambda _s, _c, view: (tmp_path/"side-front.jpg", "revision")
                        if view == "side-front" else None)
    side_front, digest3_front = ecommerce_pipeline._stock_refs(
        settings, "prod_s2_ec3", "natural-charcoal")
    assert side_front[0][0] == "stock_side_front"
    assert digest3_front != digest2 and digest3_front != digest3


def test_side_stock_cannot_be_byte_identical_to_three_quarter(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.modules.realistic_review_ugc import ecommerce_pipeline, hat_stock_sync
    settings = SimpleNamespace(CODEX_IMAGE_HOME=str(tmp_path))
    monkeypatch.setattr(hat_stock_sync, "read_managed_stock", lambda *_args: None)
    monkeypatch.setattr(ecommerce_pipeline, "read_release_stock",
                        lambda role, **kwargs: (b"same-photo", {"role": role}))
    with pytest.raises(ecommerce_pipeline.EcommerceError,
                       match="distinct side view"):
        ecommerce_pipeline._stock_refs(settings, "prod_s2_ec3", "natural-charcoal")


def test_malformed_reference_fails_validation(tmp_path):
    _stock(tmp_path, "side.jpg", valid=False)
    with pytest.raises(ValueError):
        read_release_stock("stock_side_90", project_root=tmp_path)


def test_stock_is_read_only_from_canonical_bundle(tmp_path):
    path = _stock(tmp_path, "front.jpg")
    path.unlink()
    path.symlink_to(tmp_path / "outside.jpg")
    with pytest.raises(FileNotFoundError):
        read_release_stock("stock_front", project_root=tmp_path)


def test_other_color_never_falls_back_to_natural_black(tmp_path, monkeypatch):
    from app.modules.realistic_review_ugc import hat_stock_sync
    _stock(tmp_path, "front.jpg")
    monkeypatch.setattr(hat_stock_sync, "read_managed_stock", lambda *_args: None)
    with pytest.raises(FileNotFoundError):
        read_release_stock("stock_front", project_root=tmp_path, color_key="natural-red")
    assert not release_stock_evidence(project_root=tmp_path, color_key="natural-red")["stock_front"]["ready"]


def test_unknown_stock_color_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="Unknown hat stock color"):
        read_release_stock("stock_front", project_root=tmp_path, color_key="not-a-color")