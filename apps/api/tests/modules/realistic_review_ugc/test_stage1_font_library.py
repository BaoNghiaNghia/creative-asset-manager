"""Stage 1 DaFont pool: licensed, diverse, retry-stable and anti-repeat."""
from pathlib import Path
from random import Random

from PIL import Image, ImageFont

from app.modules.realistic_review_ugc.stage1_font_library import (
    approved_font_pool, font_brief, font_info_for_number, render_font_guides,
    select_font_plan, DESIGNS_PER_JOB, RECENT_JOB_EXCLUSION_WINDOW,
)


def test_approved_pool_has_200_unique_licensed_fonts():
    pool = approved_font_pool()
    assert len(pool) >= 200
    assert len({row['sha256'] for row in pool}) == len(pool)
    assert len({row["font"] for row in pool}) == len(pool)
    assert {"Bebas Neue", "Pacifico", "Lobster"}.issubset({row["font"] for row in pool})
    for row in pool:
        font_path = Path(__file__).resolve().parents[5] / (
            "apps/api/managed_skills/gatorhats-stage1-six-designs/fonts"
        ) / row["file"]
        font = ImageFont.truetype(str(font_path), 84)
        bounds = font.getbbox("GATORHATS 8869")
        assert bounds and bounds[2] - bounds[0] > 20
    for font in pool:
        assert font["source"] == "DaFont"
        assert font["license_status"] == "approved"
        assert font["embroidery_safe"] is True
        assert font["license"].startswith("SIL OFL")
        info = next(row for row in pool if row["font"] == font["font"])
        assert Path(__file__).resolve().parents[5].joinpath(
            "deploy/codex/skills/gatorhats-stage1-six-designs/fonts", info["file"]
        ).is_file()


def test_each_job_has_six_distinct_fonts_and_avoids_last_three_jobs():
    first = select_font_plan(rng=Random(11))
    second = select_font_plan([first], rng=Random(13))
    third = select_font_plan([second, first], rng=Random(15))
    fourth = select_font_plan([third, second, first], rng=Random(17))
    for job in (first, second, third, fourth):
        assert len(job) == DESIGNS_PER_JOB
        assert len({row["font"] for row in job}) == DESIGNS_PER_JOB
        assert "design_06.png" in font_brief(job)
    assert not {row["font"] for row in first} & {row["font"] for row in second}
    assert not {row["font"] for row in second} & {row["font"] for row in third}
    assert not {row["font"] for row in fourth} & {row["font"] for row in (first + second + third)}
    assert RECENT_JOB_EXCLUSION_WINDOW == 30


def test_200_font_pool_can_avoid_thirty_full_recent_jobs():
    pool = approved_font_pool()
    assert len(pool) >= 200
    histories = [[{"font": font["font"]} for font in pool[i : i + 6]]
                 for i in range(0, 180, 6)]
    plan = select_font_plan(histories, rng=Random(21))
    assert len(plan) == 6
    blocked = {row["font"] for old in histories for row in old}
    assert len({row["font"] for row in plan}) == 6
    assert not blocked.intersection(row["font"] for row in plan)


def test_exhausted_recent_pool_falls_back_without_repeating_within_job():
    plan = select_font_plan([[{"font": row["font"]} for row in approved_font_pool()]], rng=Random(1))
    assert len(plan) == 6
    assert len({row["font"] for row in plan}) == 6


def test_real_font_guides_are_brown_transparent_and_match_report(tmp_path):
    plan = select_font_plan(rng=Random(19))
    folder = render_font_guides(tmp_path, "RESPECT YOUR MOTHER", font_plan=plan)
    guides = sorted(folder.glob("design_*.png"))
    assert len(guides) == 6
    for number, path in enumerate(guides, 1):
        report = font_info_for_number(number, font_plan=plan)
        assert report["font_name"] == plan[number - 1]["font"]
        assert report["font_source"] == "DaFont"
        assert report["font_license"].startswith("SIL OFL")
        with Image.open(path) as im:
            im.load()
            assert im.mode == "RGBA"
            assert im.size == (1600, 1150)
            assert im.getpixel((0, 0))[3] == 0
            assert im.getchannel("A").getbbox() is not None
            colors = {pixel[:3] for pixel in im.getdata() if pixel[3] >= 254}
            assert colors <= {(107, 63, 40), (116, 71, 48)}
