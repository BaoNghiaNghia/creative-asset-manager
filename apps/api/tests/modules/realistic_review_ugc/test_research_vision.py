from __future__ import annotations

import pytest

from app.modules.realistic_review_ugc.research_vision import (
    ImageMetadataParser, ResearchCapVision, fetch_public_listing_image,
    permitted_image_url, verified_quote, PublicImageUnavailable,
)


@pytest.mark.parametrize("source,url,expected", [
    ("etsy", "https://i.etsystatic.com/123/photo.jpg", True),
    ("etsy", "https://m.media-amazon.com/images/test.jpg", False),
    ("amazon", "https://m.media-amazon.com/images/test.jpg", True),
    ("pinterest", "https://i.pinimg.com/originals/a/b.jpg", True),
    ("pinterest", "https://i.pinimg.com.evil.example/pin.jpg", False),
    ("etsy", "http://i.etsystatic.com/photo.jpg", False),
    ("etsy", "https://i.etsystatic.com:8080/photo.jpg", False),
    ("etsy", "https://user:password@i.etsystatic.com/photo.jpg", False),
    ("amazon", "https://127.0.0.1/image.jpg", False),
])
def test_image_host_allowlist(source, url, expected):
    assert permitted_image_url(url, source) is expected


def test_og_image_extraction_does_not_follow_arbitrary_links():
    html = '''<html><head>
       <meta property="og:image" content="https://i.etsystatic.com/222/photo.jpg">
       <meta name="twitter:image" content="https://evil.example/banner.jpg">
       </head></html>'''
    parser = ImageMetadataParser()
    parser.feed(html)
    assert parser.images[0] == "https://i.etsystatic.com/222/photo.jpg"


def vision(**overrides):
    data = dict(
        is_baseball_cap=True, text_physically_on_cap=True, quote="Made To Worship",
        confidence=0.99, multiple_caps_or_unclear_target=False,
        shows_brand_logo_or_character=False, visual_evidence="Thread lettering on front crown.",
    )
    data.update(overrides)
    return ResearchCapVision(**data)


@pytest.mark.parametrize("patch", [
    {"confidence": 0.90},
    {"text_physically_on_cap": False},
    {"is_baseball_cap": False},
    {"multiple_caps_or_unclear_target": True},
    {"shows_brand_logo_or_character": True},
    {"quote": "Custom Embroidered Hat"},
])
def test_uncertain_or_generic_image_cannot_auto_approve(patch):
    assert verified_quote(vision(**patch), "Made To Worship Embroidered Trucker Hat") is None


def test_physical_quote_must_agree_with_source_title():
    assert verified_quote(vision(), "Made To Worship Embroidered Trucker Hat") == "MADE TO WORSHIP"
    assert verified_quote(vision(), "Funny Custom Retro Cap") is None


def test_untrusted_marketplace_url_is_rejected_without_fetch():
    import asyncio
    with pytest.raises(PublicImageUnavailable):
        asyncio.run(fetch_public_listing_image("https://localhost/pin/1234", "pinterest"))
