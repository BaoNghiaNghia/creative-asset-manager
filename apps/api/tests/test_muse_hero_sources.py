"""Independent V3.3 hero-image evidence; keep V3.1 media unchanged."""
from __future__ import annotations

from io import BytesIO
from unittest import TestCase
from PIL import Image, ImageDraw
from fastapi import HTTPException

from app.modules.muse_bridge.hero_storage import (
    MAX_HERO_BYTES, validate_hero_image, heroes_are_distinct,
)
from app.modules.muse_bridge.story_prompts import (
    STORY_SOURCE_CONTRACT, FACE_VISIBLE_MIN_SECONDS, build_story_spec,
)


def hero_bytes(width: int, height: int, *, variant: int = 0) -> bytes:
    """Native ratio imagery with genuinely different pixel compositions."""
    picture = Image.new("RGB", (width, height), (60, 90, 110))
    d = ImageDraw.Draw(picture)
    for i in range(48):
        left = (i * 91 + variant * 37) % width
        top = (i * 43 + variant * 137) % height
        right = min(width - 1, left + 10 + (i % 9) * 21)
        bottom = min(height - 1, top + 30 + (i % 11) * 17)
        color = ((i * 29 + variant * 31) % 255,
                 (i * 41 + variant * 47) % 255,
                 (i * 17 + variant * 79) % 255)
        d.rectangle((left, top, right, bottom), fill=color)
    out = BytesIO()
    picture.save(out, format="JPEG", quality=85)
    return out.getvalue()


class HeroEvidenceTests(TestCase):
    def test_native_portrait_and_landscape_have_separate_real_image_hashes(self):
        vertical = validate_hero_image(
            hero_bytes(720, 1280, variant=1), "vertical", "image/jpeg"
        )
        horizontal = validate_hero_image(
            hero_bytes(1280, 720, variant=7), "horizontal", "image/jpeg"
        )
        self.assertEqual(vertical["aspectRatio"], "9:16")
        self.assertEqual(horizontal["aspectRatio"], "16:9")
        self.assertNotEqual(vertical["sha256"], horizontal["sha256"])
        self.assertNotEqual(vertical["pixelsSha256"], horizontal["pixelsSha256"])
        self.assertTrue(heroes_are_distinct(
            {**vertical, "heroRequestId": "1" * 32, "heroSeed": 123},
            {**horizontal, "heroRequestId": "2" * 32, "heroSeed": 456},
        ))

    def test_wrong_orientation_rejected_even_if_upload_has_correct_header(self):
        image = hero_bytes(720, 1280)
        with self.assertRaises(HTTPException) as err:
            validate_hero_image(image, "horizontal", "image/jpeg")
        self.assertEqual(err.exception.status_code, 422)

    def test_duplicate_pixels_or_request_id_or_seed_rejected(self):
        a = validate_hero_image(
            hero_bytes(720, 1280, variant=1), "vertical", "image/jpeg"
        )
        b = validate_hero_image(
            hero_bytes(1280, 720, variant=7), "horizontal", "image/jpeg"
        )
        a.update(heroRequestId="1" * 32, heroSeed=123)
        b.update(heroRequestId="2" * 32, heroSeed=456)
        self.assertFalse(heroes_are_distinct(a, {**b, "sha256": a["sha256"]}))
        self.assertFalse(heroes_are_distinct(a, {**b, "pixelsSha256": a["pixelsSha256"]}))
        self.assertFalse(heroes_are_distinct(a, {**b, "heroRequestId": a["heroRequestId"]}))
        self.assertFalse(heroes_are_distinct(a, {**b, "heroSeed": a["heroSeed"]}))
        self.assertFalse(heroes_are_distinct(a, {**b, "dHash64": a["dHash64"]}))

    def test_corrupt_oversized_and_fake_content_type_rejected(self):
        with self.assertRaises(HTTPException):
            validate_hero_image(b"x" * 3000, "vertical", "image/jpeg")
        with self.assertRaises(HTTPException):
            validate_hero_image(b"x" * (MAX_HERO_BYTES + 1), "vertical", "image/jpeg")
        with self.assertRaises(HTTPException):
            validate_hero_image(hero_bytes(720, 1280), "vertical", "image/png")

    def test_distinct_aspect_shot_plans_include_face_requirement(self):
        x = build_story_spec(
            title="Christmas Nativity printed shirt",
            listing_key="4487783857", artwork_sha256="a" * 64,
        )
        self.assertEqual(x["storySourceContract"], STORY_SOURCE_CONTRACT)
        self.assertEqual(FACE_VISIBLE_MIN_SECONDS, 8)
        a = x["aspectPlans"]["9:16"]
        b = x["aspectPlans"]["16:9"]
        self.assertNotEqual(a["heroRequestId"], b["heroRequestId"])
        self.assertNotEqual(a["heroSeed"], b["heroSeed"])
        self.assertNotEqual(a["storyId"], b["storyId"])
        self.assertNotEqual(a["prompt"], b["prompt"])
        self.assertEqual(a["faceVisibleMinSeconds"], 8)
        self.assertEqual(b["faceVisibleMinSeconds"], 8)
        self.assertIn("FULL FACE", a["prompt"])
        self.assertIn("FULL FACE", b["prompt"])
        self.assertIn("native 9:16", a["prompt"])
        self.assertIn("native 16:9", b["prompt"])
        self.assertFalse(x["renderPolicy"]["sameStoryForBothRatios"])
        self.assertTrue(x["renderPolicy"]["noCrossAspectCropOrReuse"])
        self.assertEqual(x["printLock"]["artworkSha256"], "a" * 64)
        self.assertEqual(x["targetDurationSeconds"], 12)
