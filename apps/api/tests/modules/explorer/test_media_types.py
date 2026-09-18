import unittest

from app.modules.explorer.media_types import infer_media_type


class InferMediaTypeTest(unittest.TestCase):
    def test_avif_filename_overrides_generic_declared_type(self):
        self.assertEqual(
            infer_media_type("PHOTO.AVIF", "application/octet-stream; charset=binary"),
            "image/avif",
        )

    def test_valid_declared_image_type_is_normalized(self):
        self.assertEqual(
            infer_media_type("photo.avif", " IMAGE/AVIF ; charset=binary"),
            "image/avif",
        )

    def test_video_filename_recovers_a_generic_declared_type(self):
        self.assertEqual(
            infer_media_type("campaign.MP4", "application/octet-stream; charset=binary"),
            "video/mp4",
        )
