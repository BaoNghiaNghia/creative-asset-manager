"""G5 preview and raster QA, plus G6 six-slot honesty contract."""
from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from app.modules.realistic_review_ugc.typography_preview import build_typography_previews
from app.modules.realistic_review_ugc.typography_quality_gate import (
    G5_VERSION, SLOT_SCHEMA, TypographyQualityError, audit_png_slot_set,
    audit_svg_previews, inspect_typography_png, summarize_v2_slots,
)


def dna():
    return {
        "schema_version": "typography-dna/v1",
        "analyzer_version": "g3-layout-font-overlap-v1",
        "image_hash": "b" * 64,
        "typography_region": {"x": .13, "y": .18, "w": .67, "h": .54},
        "style_family": "arched-serif",
        "font_mood": {
            "primary": "serif", "secondary": None,
            "weight": "heavy", "contrast": "medium", "proportions": "wide",
        },
        "layout_graph": {"nodes": [
            {"id": "n1", "role": "primary", "path_type": "arch",
             "align": "center", "size_ratio": 1.0},
        ], "edges": []},
        "overlap_rules": [], "confidence": .92,
    }


def vector(keyword="Cà phê Việt Nam & LOVE <3"):
    return build_typography_previews(
        keyword, dna=dna(), reference_id="ref-1", dna_id="dna-1",
    )


@pytest.mark.parametrize("keyword", [
    "Cà phê Việt Nam & LOVE <3",
    "I ♥ SÀI GÒN", "SÀI GÒN   MÙA THU",
    "Big  ideas for everyone", "  A B  ",
    "24/7!", "Coffee and contemporary Vietnamese culture",
])
def test_all_g4_previews_validate_source_exactly_without_proving_image_spelling(keyword):
    result = audit_svg_previews(keyword, vector(keyword))
    assert result["version"] == G5_VERSION
    assert result["all_technical_pass"] is True
    assert result["technical_pass_count"] == 3
    assert result["image_spelling_verified"] is False
    assert result["human_review_required"] is True
    assert result["release_ready"] is False
    assert all(item["exact_source_text"] and not item["eligible_for_publication"]
               for item in result["items"])


def test_vector_mutation_is_blocked_without_masking_missing_letters():
    keyword = "Coffee & Culture"
    previews = vector(keyword)
    previews[0]["svg"] = previews[0]["svg"].replace("Culture", "Cultre")
    result = audit_svg_previews(keyword, previews)
    assert result["technical_pass_count"] == 2
    assert result["items"][0]["blocking_codes"] == ["source_keyword_mismatch"]
    assert result["release_ready"] is False


@pytest.mark.parametrize("change", [
    lambda a: a.update(svg=a["svg"].replace("</svg>", "<script>alert(1)</script></svg>")),
    lambda a: a.update(svg=a["svg"].replace("<svg ", "<svg onload=\"alert(1)\" ")),
    lambda a: a.update(svg=a["svg"].replace("viewBox=\"0 0 960 560\"", "viewBox=\"0 0 1 1\"")),
    lambda a: a.update(svg=a["svg"].replace("</svg>", "<image href=\"https://evil.invalid/x\"/></svg>")),
    lambda a: a.update(svg=a["svg"].replace("DM Serif Display", "Comic Sans MS")),
    lambda a: a.update(preview_only=False, eligible_for_generation=True),
])
def test_unsafe_or_mislabeled_svg_is_never_publishable(change):
    previews = vector("BEACH BABE")
    change(previews[0])
    result = audit_svg_previews("BEACH BABE", previews)
    assert not result["all_technical_pass"]
    assert result["items"][0]["blocking_codes"]
    assert not result["items"][0]["eligible_for_publication"]


def test_vector_duplicate_id_and_content_rejected():
    previews = vector("Coffee Culture")
    previews[1] = {**previews[1], "id": previews[0]["id"], "svg": previews[0]["svg"]}
    result = audit_svg_previews("Coffee Culture", previews)
    assert result["technical_pass_count"] == 2
    assert "duplicate_or_invalid_preview_id" in result["items"][1]["blocking_codes"]
    assert "duplicate_vector_content" in result["items"][1]["blocking_codes"]


def test_svg_previews_bounded():
    with pytest.raises(TypographyQualityError):
        audit_svg_previews("Quote", [])
    with pytest.raises(TypographyQualityError):
        audit_svg_previews("Quote", [vector("Quote")[0]] * 4)
    with pytest.raises(TypographyQualityError):
        audit_svg_previews("", vector("Quote"))


def png(*, color=(28, 80, 177, 255), box=(300, 260, 1760, 780),
        mode="RGBA", fmt="PNG"):
    image = Image.new("RGBA", (2048, 1024), (0, 0, 0, 0))
    ImageDraw.Draw(image).rounded_rectangle(box, radius=38, fill=color)
    if mode != "RGBA":
        image = image.convert(mode)
    target = BytesIO()
    image.save(target, fmt)
    return target.getvalue()


def test_png_structural_pass_still_requires_human_spelling_and_embroidery_check():
    result = inspect_typography_png(png())
    assert result["technical_pass"] is True
    assert result["blocking_codes"] == []
    assert result["perceptual_hash"] and len(result["perceptual_hash"]) == 16
    assert result["alpha_bbox"] == [300, 260, 1761, 781]
    assert result["image_spelling_verified"] is False
    assert result["eligible_for_publication"] is False
    assert "image_spelling_requires_human_review" in result["review_flags"]


def test_black_background_opaque_rgba_and_opaque_rgb_are_rejected():
    data = BytesIO()
    Image.new("RGBA", (2048, 1024), (0, 0, 0, 255)).save(data, "PNG")
    result = inspect_typography_png(data.getvalue())
    assert result["technical_pass"] is False
    assert "artwork_touches_canvas_edge" in result["blocking_codes"]
    assert "opaque_background_corner" in result["blocking_codes"]
    assert inspect_typography_png(png(mode="RGB"))["blocking_codes"] == ["missing_true_alpha"]


@pytest.mark.parametrize("item,problem", [
    (b"", "invalid_image_size"),
    (b"fake png", "png_decode_failed"),
    (png(box=(800, 510, 825, 540)), "artwork_too_small"),
    (png(box=(0, 150, 1000, 700)), "artwork_touches_canvas_edge"),
])
def test_invalid_png_image_data_is_rejected(item, problem):
    result = inspect_typography_png(item)
    assert result["technical_pass"] is False
    assert problem in result["blocking_codes"]


def test_untrusted_png_size_and_canvas_never_accepted():
    result = inspect_typography_png(b"0" * (25 * 1024 * 1024 + 1))
    assert result["blocking_codes"] == ["invalid_image_size"]
    data = BytesIO()
    Image.new("RGBA", (200, 100)).save(data, "PNG")
    assert "invalid_png_canvas" in inspect_typography_png(data.getvalue())["blocking_codes"]


def test_six_slot_png_audit_reports_missing_and_dedup_without_filling():
    one = png()
    two = png(color=(128, 24, 37, 255))
    audit = audit_png_slot_set([(3, one), (1, two), (5, one)])
    assert [item["slot"] for item in audit["items"]] == [1, 3, 5]
    assert audit["missing_slots"] == [2, 4, 6]
    assert audit["technical_pass_count"] == 2
    assert "duplicate_final_sha256" in audit["items"][2]["blocking_codes"]
    assert audit["release_ready"] is False
    assert all(not item["eligible_for_publication"] for item in audit["items"])


def test_six_slot_audit_near_duplicate_is_advisory_not_plagiarism_verdict():
    audit = audit_png_slot_set([
        (1, png(color=(33, 66, 99, 255))),
        (2, png(color=(93, 66, 99, 255))),
    ])
    assert audit["technical_pass_count"] == 2
    assert "possible_near_duplicate_review" in audit["items"][1]["review_flags"]


@pytest.mark.parametrize("entries", [
    [(1, png()), (1, png())],
    [(0, png())],
    [(7, png())],
    [(True, png())],
    [(i, png()) for i in range(1, 8)],
])
def test_bad_image_slot_numbers_fail_closed(entries):
    with pytest.raises(TypographyQualityError):
        audit_png_slot_set(entries)


def manifest(state="pending"):
    return [{"slot": i, "status": state} for i in range(1, 7)]


def good(slot):
    return {
        "slot": slot, "status": "generated", "sha256": f"{slot:064x}",
        "technical_pass": True, "review": "accepted",
        "image_spelling_verified": True,
    }


def test_v2_manifest_planned_in_progress_review_partial_and_complete():
    assert summarize_v2_slots("SÀI GÒN", manifest()) == {
        "schema_version": SLOT_SCHEMA, "source_keyword_exact": "SÀI GÒN",
        "requested_slots": 6, "eligible_slots": [], "failed_slots": [],
        "pending_slots": [1, 2, 3, 4, 5, 6], "needs_review_slots": [],
        "actual_count": 0, "partial": False, "status": "planned",
        "completed": False, "publishable": False, "manufacturing_qa_required": True,
    }
    in_progress = manifest()
    in_progress[0] = good(1)
    progress = summarize_v2_slots("SÀI GÒN", in_progress)
    assert progress["status"] == "in_progress" and progress["partial"] is True
    assert progress["publishable"] is False
    final = [good(1), *(dict(slot=i, status="failed") for i in range(2, 7))]
    partial = summarize_v2_slots("SÀI GÒN", final)
    assert partial["status"] == "partial" and partial["partial"] is True
    assert partial["actual_count"] == 1
    assert partial["publishable"] is True
    full = summarize_v2_slots("SÀI GÒN", [good(i) for i in range(1, 7)])
    assert full["status"] == "complete"
    assert full["actual_count"] == 6 and full["completed"]
    assert full["partial"] is False and full["publishable"] is True


def test_v2_manifest_never_auto_accepts_an_ai_image_claim():
    slots = [good(i) for i in range(1, 7)]
    slots[0]["image_spelling_verified"] = False
    reviewed = summarize_v2_slots("Exact quote", slots)
    assert reviewed["status"] == "needs_review"
    assert reviewed["needs_review_slots"] == [1]
    assert reviewed["actual_count"] == 5
    assert not reviewed["publishable"]
    slots[0]["technical_pass"] = False
    rejected = summarize_v2_slots("Exact quote", slots)
    assert rejected["failed_slots"] == [1]
    assert rejected["needs_review_slots"] == []
    assert rejected["status"] == "partial"
    assert rejected["actual_count"] == 5


@pytest.mark.parametrize("mutate", [
    lambda x: x.pop(),
    lambda x: x[0].update(slot=6),
    lambda x: x[0].update(slot=True),
    lambda x: x[0].update(status="completed"),
    lambda x: x[0].update(sha256="missing"),
])
def test_v2_output_manifest_rejects_bad_or_fake_slot_state(mutate):
    entries = [good(i) for i in range(1, 7)]
    mutate(entries)
    with pytest.raises(TypographyQualityError):
        summarize_v2_slots("BEACH BABE", entries)


def test_v2_output_manifest_rejects_duplicate_generated_hash():
    entries = [good(i) for i in range(1, 7)]
    entries[5]["sha256"] = entries[4]["sha256"]
    with pytest.raises(TypographyQualityError, match="duplicate image"):
        summarize_v2_slots("BEACH BABE", entries)


def test_v2_output_manifest_does_not_fake_completed_for_all_failed():
    entries = manifest("failed")
    status = summarize_v2_slots("No filler", entries)
    assert status["status"] == "failed"
    assert status["actual_count"] == 0
    assert status["failed_slots"] == list(range(1, 7))
    assert status["publishable"] is False
