"""Visor-Matched Accent V1: color-specific thread only, one durable ML mask."""
from dataclasses import replace
from io import BytesIO

import numpy as np
import pytest
from PIL import Image, ImageDraw
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import Base
from app.modules.realistic_review_ugc.colorways import (
    PRODUCTS_STAGE1_SELECTED_ROOT, _get_or_create_visor_selection,
)
from app.modules.realistic_review_ugc.model import (
    RrugcSourcePlanModel, RrugcVisorAccentSelectionModel,
)
from app.modules.realistic_review_ugc.threadtransfer import _layer_from_reference
from app.modules.realistic_review_ugc.visor_accent import (
    VISOR_THREAD_RGB, MAX_ACCENT_RATIO, VERSION,
    VisorAccentError, recolor_layer, select_accent,
)


def _reference(*, monochrome=False):
    """Six distinct needle-stitched letters; one highlighted center glyph."""
    image = Image.new("RGBA", (1000, 620))
    draw = ImageDraw.Draw(image)
    for index in range(6):
        x0 = 70 + index * 144
        pigment = ((192, 71, 40) if index == 2 and not monochrome
                   else (31, 68, 130))
        draw.rounded_rectangle((x0, 202, x0 + 92, 400), radius=12, fill=pigment+(255,))
        for x in range(x0+5, x0+90, 4):
            draw.line((x, 210, x, 390),
                      fill=tuple(min(255, n+24) for n in pigment)+(255,), width=1)
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


@pytest.mark.parametrize("monochrome", [False, True])
def test_ml_auto_selection_preserves_complete_thread_only_area(monochrome):
    layer, _ = _layer_from_reference(_reference(monochrome=monochrome))
    chosen = select_accent(layer)
    assert chosen.method in {"lab-kmeans-chromatic", "connected-lettering"}
    assert 0.04 <= chosen.ratio <= MAX_ACCENT_RATIO
    assert chosen.selected_pixels >= 100
    assert len(chosen.layer_sha256) == 64
    assert chosen.mask_png.startswith(b"\x89PNG")


def test_all_twelve_visor_palettes_have_contrast_and_only_mask_changes():
    layer, _ = _layer_from_reference(_reference())
    original = np.asarray(layer)
    chosen = select_accent(layer)
    stock = Image.new("RGB", (1100, 1200), (231, 218, 194))
    for color in VISOR_THREAD_RGB:
        recolored, qa = recolor_layer(layer, chosen, color, stock, (235, 290, 870, 720))
        current = np.asarray(recolored)
        with Image.open(BytesIO(chosen.mask_png)) as opened:
            selected = np.asarray(opened) > 127
        assert qa["status"] == "applied"
        assert qa["max_ratio"] == 0.30
        assert qa["accent_ratio"] <= 0.30
        assert qa["crown_contrast"] >= 2.5
        assert qa["main_thread_lab_delta"] >= 19
        assert qa["changed_pixels"] >= chosen.selected_pixels * 0.35
        assert np.array_equal(current[:, :, 3], original[:, :, 3])
        assert np.array_equal(current[:, :, :3][~selected], original[:, :, :3][~selected])
        assert np.count_nonzero(np.any(current[:, :, :3] != original[:, :, :3], axis=2) & ~selected) == 0


def test_mask_evidence_rejected_if_layer_or_pixel_limit_changed():
    layer, _ = _layer_from_reference(_reference())
    chosen = select_accent(layer)
    wrong = replace(chosen, layer_sha256="0" * 64)
    with pytest.raises(VisorAccentError, match="does not match"):
        recolor_layer(layer, wrong, "natural-red",
                      Image.new("RGB", (900, 1000), "ivory"), (100, 180, 700, 600))
    wrong = replace(chosen, selected_pixels=chosen.thread_pixels)
    with pytest.raises(VisorAccentError, match="Invalid or over-limit"):
        recolor_layer(layer, wrong, "natural-red",
                      Image.new("RGB", (900, 1000), "ivory"), (100, 180, 700, 600))


def test_one_material_one_persisted_selection_and_new_reference_is_independent(monkeypatch):
    from app.modules.realistic_review_ugc import colorways
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        RrugcSourcePlanModel.__table__, RrugcVisorAccentSelectionModel.__table__,
    ])
    factory = sessionmaker(bind=engine)
    with factory() as session:
        session.add(RrugcSourcePlanModel(
            id="source", tenant_id="tenant", root_folder_id=PRODUCTS_STAGE1_SELECTED_ROOT,
            source_file_id="src-png", source_revision="revision-1",
            source_name="design.png", source_relative_path="design.png",
            source_mime_type="image/png", created_by_user_id="actor",
        ))
        session.commit()
    reference = _reference()
    layer, _ = _layer_from_reference(reference)
    original = colorways.select_accent
    calls = []
    def counted(image):
        calls.append(True)
        return original(image)
    monkeypatch.setattr(colorways, "select_accent", counted)
    def create(name):
        return _get_or_create_visor_selection(
            factory, tenant_id="tenant", source_plan_id="source",
            source_revision="revision-1", material_remote_file_id=name,
            reference_png=reference, thread_layer=layer,
        )
    a, hit_a = create("saved-slot1-macro")
    b, hit_b = create("saved-slot1-macro")
    c, hit_c = create("new-slot1-macro")
    assert not hit_a and hit_b and not hit_c
    assert len(calls) == 2
    assert a.mask_png == b.mask_png == c.mask_png
    with factory() as session:
        assert session.query(RrugcVisorAccentSelectionModel).count() == 2
        assert {row.algorithm_version for row in session.query(RrugcVisorAccentSelectionModel)} == {VERSION}
    engine.dispose()


@pytest.mark.parametrize("color_key", sorted(VISOR_THREAD_RGB))
def test_visors_match_actual_official_factory_stock_contrast(color_key):
    """QA the selected accent against real 8869 crown reference per color."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[5]
    stock_path = (root / "deploy/codex/skills/gatorhats-8869-image-studio"
                  / "assets/stock" / color_key / "front.jpg")
    with Image.open(stock_path) as image:
        stock = image.convert("RGB")
    guide = (round(stock.width * .23), round(stock.height * .30),
             round(stock.width * .77), round(stock.height * .58))
    layer, _ = _layer_from_reference(_reference())
    _result, qa = recolor_layer(layer, select_accent(layer), color_key, stock, guide)
    assert qa["status"] == "applied"
    assert qa["crown_contrast"] >= 2.5
    assert qa["accent_ratio"] <= 0.30
