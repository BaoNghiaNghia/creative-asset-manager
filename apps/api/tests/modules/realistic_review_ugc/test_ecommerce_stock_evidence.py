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
    with pytest.raises(ValueError):
        read_release_stock("stock_three_quarter", project_root=tmp_path)
    assert release_stock_evidence(project_root=tmp_path)["stock_three_quarter"]["ready"] is False
    assert release_stock_evidence(project_root=tmp_path)["stock_front"]["ready"] is False


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
