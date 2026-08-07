import io
import unittest

from PIL import Image, features

from app.modules.explorer.preview import (
    AVIF_DECODER_AVAILABLE,
    PreviewConversionError,
    convert_avif_to_webp,
)


@unittest.skipUnless(AVIF_DECODER_AVAILABLE, "Pillow AVIF decoder is unavailable")
class AvifPreviewConversionTest(unittest.TestCase):
    def _avif(self, image: Image.Image, **kwargs) -> bytes:
        output = io.BytesIO()
        image.save(output, format="AVIF", **kwargs)
        return output.getvalue()

    def test_runtime_has_avif_decoder_and_returns_webp(self):
        self.assertTrue(features.check("avif"))
        content = convert_avif_to_webp(self._avif(Image.new("RGB", (80, 40), "red")))
        with Image.open(io.BytesIO(content)) as decoded:
            self.assertEqual(decoded.format, "WEBP")
            self.assertEqual(decoded.size, (80, 40))

    def test_sequence_uses_first_frame(self):
        first = Image.new("RGB", (20, 10), "red")
        second = Image.new("RGB", (20, 10), "blue")
        content = convert_avif_to_webp(self._avif(first, save_all=True, append_images=[second], duration=100))
        with Image.open(io.BytesIO(content)) as decoded:
            self.assertGreater(decoded.getpixel((5, 5))[0], decoded.getpixel((5, 5))[2])

    def test_large_image_is_resized_without_upscaling(self):
        content = convert_avif_to_webp(self._avif(Image.new("RGB", (3200, 1600), "green")))
        with Image.open(io.BytesIO(content)) as decoded:
            self.assertEqual(decoded.size, (1600, 800))

    def test_invalid_avif_is_structured_conversion_failure(self):
        with self.assertRaises(PreviewConversionError):
            convert_avif_to_webp(b"not-an-avif")
