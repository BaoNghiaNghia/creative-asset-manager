"""Approved DaFont library and per-job non-repeating font plans for Stage 1.

Never scrape or download fonts in a live job. Only locally bundled, verified
commercial-usable fonts can be selected. Plans are stored on jobs for retries.
"""
from __future__ import annotations

import hashlib
import json
import random
from functools import lru_cache
from pathlib import Path
from typing import Any

SKILL_NAME = "gatorhats-stage1-six-designs"
FONTS_DIR = Path(__file__).resolve().parents[3] / "managed_skills" / SKILL_NAME / "fonts"
REGISTRY_PATH = FONTS_DIR.parent / "font_registry.json"
LAYOUTS = (
    "Balanced bold headline and stacked subline",
    "Compact condensed layout, varied line lengths",
    "Flowing centered typography with balanced whitespace",
    "Retro typographic hierarchy and clean line breaks",
    "Strong collegiate-style word hierarchy without decorations",
    "Natural contrasting word scale, compact composition",
)
RECENT_JOB_EXCLUSION_WINDOW = 30
RECENT_JOB_LOOKBACK = 30
DESIGNS_PER_JOB = 6

# Older published outputs can still be inspected; never silently relabel
# them as if their job had used a newly-randomized font.
_LEGACY_SEQUENCE = ("Bebas Neue", "Bebas Neue", "Pacifico", "Lobster", "Bebas Neue", "Lobster")


@lru_cache(maxsize=1)
def approved_font_pool() -> tuple[dict[str, Any], ...]:
    document = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    if document.get("source") != "DaFont":
        raise ValueError("Stage 1 fonts must originate from DaFont")
    fonts = []
    seen: set[str] = set()
    for row in document["fonts"]:
        if (not row.get("enabled") or not row.get("embroidery_safe")
                or row.get("license_status") != "approved"
                or not str(row.get("license", "")).startswith("SIL OFL")):
            continue
        if row["font"] in seen:
            raise ValueError("Duplicate DaFont family in registry")
        font_file = FONTS_DIR / row["file"]
        license_file = FONTS_DIR / row["license_file"]
        if (not font_file.is_file() or not license_file.is_file()
                or hashlib.sha256(font_file.read_bytes()).hexdigest() != row["sha256"]):
            raise ValueError(f"DaFont approved asset missing or modified: {row['font']}")
        license_text = license_file.read_text(encoding="utf-8", errors="replace").upper()
        if "SIL OPEN FONT LICENSE" not in license_text or "VERSION 1.1" not in license_text:
            raise ValueError(f"DaFont font is missing its OFL license text: {row['font']}")
        seen.add(row["font"])
        fonts.append(row)
    if len(fonts) < DESIGNS_PER_JOB:
        raise ValueError("At least six different approved DaFont families are required")
    return tuple(fonts)


def select_font_plan(
    recent_plans: list[list[dict[str, Any]]] | None = None,
    *,
    rng: random.Random | None = None,
    pool: tuple[dict[str, Any], ...] | None = None,
) -> list[dict[str, Any]]:
    """Six distinct families, maximizing non-repetition across recent jobs.

    With 200 approved families, six-per-job generation can usually avoid the
    preceding 30 full jobs. If historical usage exhausts eligible families,
    relax the exclusion window without blocking generation; recency penalties
    still discourage overused fonts.
    """
    source = tuple(pool if pool is not None else approved_font_pool())
    if len({row["font"] for row in source}) < DESIGNS_PER_JOB:
        raise ValueError("Not enough unique approved font families")
    histories = recent_plans or []
    eligible = list(source)
    for window in range(min(RECENT_JOB_EXCLUSION_WINDOW, len(histories)), -1, -1):
        blocked = {record["font"] for batch in histories[:window] for record in batch}
        candidates = [row for row in source if row["font"] not in blocked]
        if len(candidates) >= DESIGNS_PER_JOB:
            eligible = candidates
            break
    counts = {
        row["font"]: sum(row["font"] == old.get("font")
                         for batch in histories[:RECENT_JOB_LOOKBACK] for old in batch)
        for row in source
    }
    random_source = rng if rng is not None else random.SystemRandom()
    choices: list[dict[str, Any]] = []
    for number in range(DESIGNS_PER_JOB):
        used_categories = {row["category"] for row in choices}
        scored = sorted(
            (
                (float(row.get("score", 7)) + (6 if row["category"] not in used_categories else 0)
                 - counts[row["font"]] * 9
                 + random_source.random() * 7, row)
                for row in eligible if row["font"] not in {picked["font"] for picked in choices}
            ),
            key=lambda item: item[0], reverse=True,
        )
        chosen = scored[0][1].copy()
        chosen["layout"] = LAYOUTS[number]
        choices.append(chosen)
    return choices


def font_info_for_number(number: int, *, font_plan: list[dict] | tuple[dict, ...] | None = None,
                         font_dir: Path | None = None) -> dict:
    if not 1 <= number <= DESIGNS_PER_JOB:
        raise ValueError("Stage 1 design index must be 1..6")
    if font_plan is None:
        # Backward compatibility for existing output reports with pre-pool jobs.
        family = _LEGACY_SEQUENCE[number - 1]
        entry = next(row for row in approved_font_pool() if row["font"] == family)
        layout = LAYOUTS[number - 1]
    else:
        if len(font_plan) != DESIGNS_PER_JOB:
            raise ValueError("Stored DaFont plan must contain exactly six fonts")
        entry = font_plan[number - 1]
        layout = entry["layout"]
    if not (font_dir or FONTS_DIR).joinpath(entry["file"]).is_file():
        raise FileNotFoundError("Bundled Stage 1 font missing: " + entry["file"])
    return {
        "font_name": entry["font"], "font_style": entry["group"],
        "layout_name": layout, "font_source": "DaFont",
        "font_url": entry["url"], "font_license": entry["license"],
        "font_file": entry["file"], "font_status": "layout_reference",
    }


def font_brief(font_plan: list[dict] | tuple[dict, ...] | None = None) -> str:
    if not font_plan:
        return (
            "A persistent six-font DaFont plan is selected at job dispatch. "
            "Use the exact local font guide for design_01.png through design_06.png. "
            "Do not reuse a fixed set of three fonts."
        )
    return " ".join(
        f"design_{index:02}.png: {item['font']} via DaFont; "
        f"FONT fonts/{item['file']}; LAYOUT {item['layout']}; "
        f"LICENSE {item['license']}."
        for index, item in enumerate(font_plan, 1)
    )


def ensure_job_font_plan(session, job) -> list[dict[str, Any]]:
    """Persist once before Codex starts, stable on retry/resume.

    The job row is locked by the worker to prevent divergent assignments.
    On explicit Regenerate the caller resets job.font_plan_json.
    """
    if job.font_plan_json is not None:
        if len(job.font_plan_json) != DESIGNS_PER_JOB:
            raise ValueError("Invalid persisted six-font plan")
        return job.font_plan_json
    from sqlalchemy import select
    from app.modules.realistic_review_ugc.model import (
        RrugcKeywordImageJobModel, RrugcImageOutputVersionModel,
    )

    recent_rows = session.scalars(
        select(RrugcKeywordImageJobModel)
        .where(
            RrugcKeywordImageJobModel.tenant_id == job.tenant_id,
            RrugcKeywordImageJobModel.id != job.id,
            RrugcKeywordImageJobModel.font_plan_json.is_not(None),
        )
        .order_by(RrugcKeywordImageJobModel.updated_at.desc())
        .limit(RECENT_JOB_LOOKBACK)
    ).all()
    histories = [row.font_plan_json for row in recent_rows if row.font_plan_json]
    # Explicit regenerate of the same job should try fresh families, too.
    old_outputs = session.scalars(
        select(RrugcImageOutputVersionModel.font_info_json)
        .where(
            RrugcImageOutputVersionModel.tenant_id == job.tenant_id,
            RrugcImageOutputVersionModel.stage == "stage1",
            RrugcImageOutputVersionModel.job_id == job.id,
            RrugcImageOutputVersionModel.font_info_json.is_not(None),
        ).order_by(RrugcImageOutputVersionModel.created_at.desc()).limit(DESIGNS_PER_JOB)
    ).all()
    previous_batch = [{"font": info["font_name"]} for info in old_outputs if info and info.get("font_name")]
    if previous_batch:
        histories.insert(0, previous_batch)
    job.font_plan_json = select_font_plan(histories)
    session.flush()
    return job.font_plan_json


def render_font_guides(
    workspace: Path, quote: str, *, font_dir: Path | None = None,
    font_plan: list[dict] | tuple[dict, ...] | None = None,
) -> Path:
    """Rasterize exact chosen DaFont glyphs as brown, transparent working inputs."""
    from PIL import Image, ImageDraw, ImageFont

    if not quote or len(quote) > 500:
        raise ValueError("Stage 1 quote must be 1..500 characters")
    plan = list(font_plan) if font_plan is not None else select_font_plan()
    if len(plan) != DESIGNS_PER_JOB or len({row["font"] for row in plan}) != DESIGNS_PER_JOB:
        raise ValueError("Stage 1 font plan must contain six unique families")
    base = font_dir or FONTS_DIR
    target = workspace / "working" / "font-guides"
    target.mkdir(parents=True, exist_ok=True)
    words = quote.split()
    for number, entry in enumerate(plan, start=1):
        font_file = base / entry["file"]
        if not font_file.is_file():
            raise FileNotFoundError("Font library is missing " + entry["file"])
        columns = 2 if number in (1, 3, 6) else 3
        if len(words) <= 2:
            lines = words if number in (2, 4, 5) else [" ".join(words)]
        else:
            groups = min(columns, len(words))
            lines = [
                " ".join(words[i * len(words) // groups : (i + 1) * len(words) // groups])
                for i in range(groups)
            ]
        if number in (1, 4) and len(words) > 2:
            lines = [words[0], " ".join(words[1:])]
        width, height = 1600, 1150
        margin = 125
        font_size = 320
        while font_size >= 64:
            font = ImageFont.truetype(str(font_file), font_size)
            probe = ImageDraw.Draw(Image.new("L", (width, height)))
            measures = [probe.textbbox((0, 0), line, font=font) for line in lines]
            widths = [bounds[2] - bounds[0] for bounds in measures]
            tallest = max(bounds[3] - bounds[1] for bounds in measures)
            gap = int(font_size * .08)
            if max(widths) <= width - 2 * margin and len(lines) * (tallest + gap) <= height - 2 * margin:
                break
            font_size -= 8
        mask = Image.new("L", (width, height))
        draw = ImageDraw.Draw(mask)
        line_height = tallest + int(font_size * .08)
        top = (height - line_height * len(lines)) // 2
        for i, line in enumerate(lines):
            bounds = draw.textbbox((0, 0), line, font=font)
            x = (width - (bounds[2] - bounds[0])) // 2 - bounds[0]
            y = top + i * line_height - bounds[1]
            draw.text((x, y), line, font=font, fill=255)
        pigment = Image.new("RGBA", (width, height), (107, 63, 40, 255))
        pigment_draw = ImageDraw.Draw(pigment)
        for diag in range(-height, width + height, 9):
            pigment_draw.line([(diag, 0), (diag + height, height)],
                              fill=(116, 71, 48, 255), width=2)
        pigment.putalpha(mask)
        pigment.save(target / f"design_{number:02}.png", "PNG")
    return target
