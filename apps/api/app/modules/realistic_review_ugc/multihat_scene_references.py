"""Tenant-scoped, reproducible Cap Stack scene selection for Products Skill 5.

The pinned 20-photo sample lives in each processing job payload. Image bytes
are fetched only by the worker and collapsed into small contact sheets so
imagegen can inspect all compositions without 20 full-resolution inputs.
"""
from __future__ import annotations

from collections.abc import Sequence
from hashlib import sha256
from io import BytesIO
import random
from uuid import uuid4

import httpx
from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.modules.image_generation.providers import ReferenceImageInput
from app.modules.realistic_review_ugc.cap_stack_model import RrugcCapStackImageModel
from app.modules.realistic_review_ugc.generation_handler import _prepared_image
from app.modules.realistic_review_ugc.ugc_scene_selection import allowed_scout_image_url


SCENE_SAMPLE_SIZE = 20
MIN_SCENE_REFERENCES = 4
SHEET_IMAGE_COUNT = 5
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024
MAX_SOURCE_PIXELS = 20_000_000
_ALLOWED_MIMES = frozenset(("image/jpeg", "image/png", "image/webp"))


class MultiHatReferencesUnavailable(RuntimeError):
    pass


def select_multihat_references(
    session: Session, *, tenant_id: str, source_plan_id: str,
    source_revision: str, random_key: str | None = None,
) -> list[dict]:
    """Randomly sample up to 20 validated 3/4-cap photographs per design/run.

    Prefer ten per cap count; fill remaining places from the other count.
    Use bounded reservoir sampling rather than expensive ORDER BY random()
    or loading the entire tenant library into memory. Human rejected/AI
    images and unverified vision guesses are never selected.
    """
    model = RrugcCapStackImageModel
    cap_count = func.coalesce(model.manual_caps_count, model.caps_detected)
    candidates = select(model).where(
        model.tenant_id == tenant_id,
        cap_count.in_((3, 4)),
        or_(
            model.manual_label == "good",
            and_(
                model.manual_label.is_(None),
                model.count_status == "ready",
                model.is_cap_stack.is_(True),
                model.ai_suspected.is_(False),
            ),
        ),
    ).order_by(model.id)
    key = random_key or str(uuid4())
    seed = int.from_bytes(sha256(
        f"{tenant_id}:{source_plan_id}:{source_revision}:{key}:multi-hat".encode()
    ).digest(), "big")
    rng = random.Random(seed)
    sample: dict[int, list[dict]] = {3: [], 4: []}
    counts = {3: 0, 4: 0}
    seen: set[str] = set()

    for row in session.scalars(candidates.execution_options(yield_per=250)):
        url = str(row.image_url or "")
        identity = str(row.pin_key or row.id)
        if not allowed_scout_image_url(url) or identity in seen:
            continue
        seen.add(identity)
        number = row.manual_caps_count if row.manual_caps_count is not None else row.caps_detected
        if number not in (3, 4):
            continue
        counts[number] += 1
        item = {
            "image_id": row.id,
            "pin_url": row.pin_url,
            "image_url": url,
            "caps_count": number,
        }
        bucket = sample[number]
        if len(bucket) < SCENE_SAMPLE_SIZE:
            bucket.append(item)
        else:
            index = rng.randrange(counts[number])
            if index < SCENE_SAMPLE_SIZE:
                bucket[index] = item

    if sum(counts.values()) < MIN_SCENE_REFERENCES:
        raise MultiHatReferencesUnavailable(
            "Need at least four verified 3/4-hat Cap Stack images in GatorHats Reviews Stage 1."
        )
    rng.shuffle(sample[3])
    rng.shuffle(sample[4])
    selected = sample[3][:10] + sample[4][:10]
    leftovers = sample[3][10:] + sample[4][10:]
    rng.shuffle(leftovers)
    selected += leftovers[:max(0, SCENE_SAMPLE_SIZE - len(selected))]
    rng.shuffle(selected)
    # When exactly 20 images exist, selecting all 20 for every design would
    # produce identical sets. Leave one out so separate designs can differ.
    # With fewer than four, fail above instead of diluting the sample further.
    target_size = (
        SCENE_SAMPLE_SIZE if sum(counts.values()) > SCENE_SAMPLE_SIZE
        else max(MIN_SCENE_REFERENCES, sum(counts.values()) - 1)
    )
    return selected[:target_size]


def validate_pinned_multihat_references(value: object) -> list[dict]:
    """Reject malformed persisted payloads without trusting client-provided URLs."""
    if not isinstance(value, list) or not MIN_SCENE_REFERENCES <= len(value) <= SCENE_SAMPLE_SIZE:
        return []
    validated: list[dict] = []
    identities: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            return []
        image_id, image_url = item.get("image_id"), item.get("image_url")
        if (not isinstance(image_id, str) or not image_id or
                not isinstance(image_url, str) or not allowed_scout_image_url(image_url) or
                item.get("caps_count") not in (3, 4) or image_id in identities):
            return []
        identities.add(image_id)
        validated.append({
            "image_id": image_id,
            "image_url": image_url,
            "pin_url": str(item.get("pin_url") or ""),
            "caps_count": item["caps_count"],
        })
    return validated


def _scene_tile(data: bytes) -> Image.Image:
    with Image.open(BytesIO(data)) as opened:
        width, height = opened.size
        if width < 1 or height < 1 or width * height > MAX_SOURCE_PIXELS:
            raise ValueError("Cap Stack scene exceeds the allowed pixel count")
        oriented = ImageOps.exif_transpose(opened)
        oriented.load()
        return ImageOps.contain(oriented.convert("RGB"), (492, 368), Image.Resampling.LANCZOS)


async def build_multihat_reference_sheets(pinned: Sequence[dict]) -> tuple[list[ReferenceImageInput], int]:
    """Download only allowlisted Pinterest images; return sheets of five scenes.

    A broken CDN photo is skipped. We need at least four valid source
    compositions, otherwise the worker may retry the *same pinned selection*.
    """
    validated = validate_pinned_multihat_references(list(pinned))
    if not validated:
        raise MultiHatReferencesUnavailable("Pinned 3/4-hat references are unavailable.")

    tiles: list[tuple[Image.Image, int]] = []
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(18.0, connect=7.0),
        trust_env=False, follow_redirects=False,
    ) as client:
        for item in validated:
            try:
                async with client.stream("GET", item["image_url"]) as response:
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").split(";")[0].lower()
                    if content_type not in _ALLOWED_MIMES:
                        continue
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_DOWNLOAD_BYTES:
                            raise ValueError("Reference image is too large")
                tile = _scene_tile(bytes(content))
                tiles.append((tile, item["caps_count"]))
            except (httpx.HTTPError, OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
                continue

    if len(tiles) < MIN_SCENE_REFERENCES:
        raise MultiHatReferencesUnavailable(
            "Fewer than four pinned Cap Stack reference images could be downloaded."
        )
    sheets: list[ReferenceImageInput] = []
    for start in range(0, len(tiles), SHEET_IMAGE_COUNT):
        group = tiles[start:start + SHEET_IMAGE_COUNT]
        board = Image.new("RGB", (1040, 1320), "#f2f5fa")
        draw = ImageDraw.Draw(board)
        for index, (image, cap_count) in enumerate(group):
            x = 12 + (index % 2) * 516
            y = 12 + (index // 2) * 432
            draw.rectangle((x, y, x + 503, y + 417), fill="#ffffff")
            board.paste(image, (x + (504 - image.width) // 2, y + (368 - image.height) // 2))
            draw.text((x + 12, y + 386), f"Scene {start + index + 1:02d} - {cap_count} hats", fill="#222e45")
            image.close()
        data = BytesIO()
        board.save(data, format="JPEG", quality=84, optimize=True)
        board.close()
        sheets.append(ReferenceImageInput(
            image=_prepared_image(data.getvalue()),
            role="multihat_scene_contact_sheet",
            label=f"Real Cap Stack photographs {start + 1}-{start + len(group)}: composition only",
        ))
    return sheets, len(tiles)
