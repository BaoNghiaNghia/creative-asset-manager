import pytest

from app.modules.realistic_review_ugc.research_keyword_extraction import extract_listing_quote


@pytest.mark.parametrize("title,expected", [
    ("Good Taste in Music Bad Taste in Men Trucker Hat", "GOOD TASTE IN MUSIC BAD TASTE IN MEN"),
    ("Embroidered Rope Hat: Bad Day to Be A Beer Snapback ...", "BAD DAY TO BE A BEER"),
    ("Created With a Purpose Embroidered Hat, Christian Faith ...", "CREATED WITH A PURPOSE"),
    ("Touchdown Season Hat Funny Saying (Embroidered Trucker Cap) Black", "TOUCHDOWN SEASON"),
    ("Best Husband Ever Trucker Hat, Funny Husband Gift Cap", "BEST HUSBAND EVER"),
    ("Embroidered \"be Still and Know\" Baseball Cap", "BE STILL AND KNOW"),
    ("Keep Going Embroidered Baseball Caps, Positive Quote", "KEEP GOING"),
    ("Custom Toddler Baseball Hat Boys Girls Personalized", None),
    ("Balenciaga Hat Cap Baseball 2017 Winter Archetype Fall 17 Black - Etsy", None),
    ("Raylarnia Kermit The Frog Embroidered Ball Cap", None),
    ("Ny cap | Couple goals teenagers pictures, Cap girl", None),
    ("Embroidered Yellowstone National Park Five Panel Cap | Adventure Camper Hat", None),
    ("Biohazard Symbol Embroidered Baseball Cap - Red", None),
    ("NOTHING Foam Trucker Hat", None),
    ("Embroidered Custom Quote Golf Rope Hat", None),
    ("WESTERN OUTFITS FOR WOMEN TRUCKER HATS", None),
])
def test_conservative_listing_extraction(title, expected):
    assert extract_listing_quote(title, "etsy", "auto-discover") == expected


def test_no_auto_keyword_from_manual_or_unrelated_search_signal():
    assert extract_listing_quote("MADE TO WORSHIP Embroidered Hat", "rss", "auto-discover") is None
    assert extract_listing_quote("MADE TO WORSHIP Embroidered Hat", "etsy", "manual") is None
    assert extract_listing_quote("MADE TO WORSHIP cotton shirt", "etsy", "auto-discover") is None
