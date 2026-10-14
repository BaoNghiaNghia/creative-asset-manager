"""Offline DaFont importer regression: no fake licenses or unusable glyphs."""
from importlib.util import module_from_spec, spec_from_file_location
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[5]
SPEC = spec_from_file_location("cam_stage1_dafont_expand", ROOT / "scripts/cam_stage1_dafont_expand.py")
assert SPEC and SPEC.loader
MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def package(font: bytes, license_text: bytes, *, filename: str = "Sample-Regular.ttf") -> bytes:
    result = BytesIO()
    with ZipFile(result, mode="w") as archive:
        archive.writestr(filename, font)
        archive.writestr("OFL.txt", license_text)
    return result.getvalue()


def test_valid_open_font_zip_can_supply_real_usable_font():
    source = ROOT / "apps/api/managed_skills/gatorhats-stage1-six-designs/fonts"
    font = (source / "BebasNeue-Regular.ttf").read_bytes()
    license_text = (source / "BebasNeue-OFL.txt").read_bytes()
    content, proof, is_otf = MODULE.extract_approved_zip(package(font, license_text))
    assert content == font
    assert proof == license_text
    assert is_otf is False


def test_reject_license_label_only_and_unrenderable_assets():
    source = ROOT / "apps/api/managed_skills/gatorhats-stage1-six-designs/fonts"
    font = (source / "BebasNeue-Regular.ttf").read_bytes()
    fake = b"SIL OPEN FONT LICENSE Version 1.1: not a full license"
    with pytest.raises(ValueError, match="No bundled full SIL"):
        MODULE.extract_approved_zip(package(font, fake))
    full_license = (source / "BebasNeue-OFL.txt").read_bytes()
    with pytest.raises(ValueError, match="No legible"):
        MODULE.extract_approved_zip(package(b"not-a-font" * 5000, full_license))


def test_catalog_parsing_provides_exact_dafont_identity():
    snippet = (
        '<div class="lv1left dfbg"><a href="comfortaa.font?fpp=50">'
        '<strong>Comfortaa</strong></a></div>'
        '<div class="lv2right">Public domain / GPL / OFL</div>'
        '<div class="dlbox"><a class="dl" href="//dl.dafont.com/dl/?f=comfortaa">Download</a></div>'
    ).encode()
    fonts = MODULE.candidates_from_page(snippet, "sans", "sans-serif")
    assert fonts == [{
        "font": "Comfortaa", "url": "https://www.dafont.com/comfortaa.font",
        "zip_name": "comfortaa", "group": "sans-serif", "category": "sans",
    }]
