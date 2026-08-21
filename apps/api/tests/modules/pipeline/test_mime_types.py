import unittest

from app.modules.pipeline.mime_types import (
    is_supported_image_mime_type,
    is_supported_video_mime_type,
    normalize_source_mime_type,
)


class ImageMimeTypesTest(unittest.TestCase):
    def test_avif_heic_and_heif_are_canonical_supported_images(self):
        for value in ("image/avif", "image/heic", "image/heif", "image/heic-sequence", "image/heif-sequence"):
            with self.subTest(value=value):
                self.assertTrue(is_supported_image_mime_type(value))

    def test_normalization_is_lowercase_and_whitespace_safe(self):
        self.assertEqual(normalize_source_mime_type(" Image/HEIC "), "image/heic")
        self.assertTrue(is_supported_image_mime_type(" Image/HEIF "))
        self.assertFalse(is_supported_video_mime_type(" image/heic "))

    def test_existing_non_image_types_remain_excluded(self):
        self.assertFalse(is_supported_image_mime_type("image/x-photoshop"))
        self.assertFalse(is_supported_image_mime_type("video/mp4"))
