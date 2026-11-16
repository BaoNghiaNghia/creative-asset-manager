"""Pure-Python V10 text-region regressions; no image dependencies or data writes."""
from __future__ import annotations

import unittest

from app.modules.etsy_spy.color_match_v10 import _refine_small_text


def _pixels(width=128, height=128):
    return [(0, 0, 0, 0) for _ in range(width * height)]


def _paint(pixels, x0, y0, x1, y1, value, alpha=255, width=128):
    for y in range(y0, y1):
        for x in range(x0, x1):
            pixels[y * width + x] = (value, value, value, alpha)


class V10IndependentTextRoiTests(unittest.TestCase):
    def test_two_lines_same_family_have_independent_contrast_probes(self):
        pixels = _pixels()
        _paint(pixels, 4, 9, 45, 21, 8)
        _paint(pixels, 4, 72, 45, 90, 100)
        regions = [
            {"kind": "probable_text", "pigment": "dark-ink",
             "bbox": [30, 65, 350, 185]},
            {"kind": "probable_text", "pigment": "dark-ink",
             "bbox": [30, 550, 350, 730]},
        ]
        refined, micro_count = _refine_small_text(
            pixels, 128, 128, regions, [("dark-ink", .04, 4.5, 1.0)])
        self.assertEqual(len(refined), 2)
        self.assertGreater(max(row[1] for row in refined)
                           - min(row[1] for row in refined), .10)
        self.assertEqual(micro_count, 0)  # headline ROIs are still analyzed

    def test_microtext_diagnostics_do_not_discard_large_headlines(self):
        pixels = _pixels()
        _paint(pixels, 5, 9, 45, 17, 12)
        _paint(pixels, 5, 55, 45, 82, 91, alpha=160)
        regions = [
            {"kind": "probable_text", "pigment": "dark-ink",
             "bbox": [35, 60, 360, 135]},
            {"kind": "probable_text", "pigment": "dark-ink",
             "bbox": [35, 415, 360, 655]},
        ]
        refined, micro_count = _refine_small_text(
            pixels, 128, 128, regions, [("dark-ink", .01, 4.5, 1.0)])
        self.assertEqual(len(refined), 2)
        self.assertEqual(micro_count, 1)
        self.assertLess(min(row[3] for row in refined), .7)

    def test_significant_two_tone_stroke_keeps_both_luminance_bounds(self):
        pixels = _pixels()
        _paint(pixels, 4, 30, 24, 49, 9)
        _paint(pixels, 24, 30, 44, 49, 98)
        regions = [{"kind": "probable_text", "pigment": "dark-ink",
                    "bbox": [20, 220, 360, 405]}]
        refined, _ = _refine_small_text(
            pixels, 128, 128, regions, [("dark-ink", .03, 4.5, 1.0)])
        self.assertEqual(len(refined), 2)
        self.assertGreater(refined[1][1] - refined[0][1], .10)

    def test_critical_illustration_region_is_not_promoted_to_text(self):
        pixels = _pixels()
        _paint(pixels, 10, 10, 100, 100, 100)
        regions = [{"kind": "critical_ink", "pigment": "dark-ink",
                    "bbox": [50, 50, 900, 900]}]
        fallback = ("dark-ink", .04, 3.0, 1.0)
        refined, inspected = _refine_small_text(pixels, 128, 128, regions, [fallback])
        self.assertEqual(refined, [fallback])
        self.assertEqual(inspected, 0)


if __name__ == "__main__":
    unittest.main()
