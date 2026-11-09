"""Keyword SMS title fallback: source evidence always wins; no database rewrite."""
from types import SimpleNamespace

from app.modules.etsy_spy.router import _listing_item
from app.modules.etsy_spy.service import _payload_fields, title_keyword_fallback


def _listing(title, metadata):
    return SimpleNamespace(
        source_session_id="test", keyword="Grand Teton", source_kind="search",
        has_search=True, has_shop=False, listing_key="4564121744",
        title=title, url="https://www.etsy.com/listing/4564121744/test",
        badge="Bestseller", search_badge="Bestseller",
        source_shop_url=None, shop_position=None, search_position=None,
        bought24h=None, views24h=19, in_carts=20, demand_score=None,
        bestseller=True, observed_at=None, metadata_json=metadata,
    )


def test_title_keyword_uses_first_clause():
    assert title_keyword_fallback(
        "Grand Teton National Park Sweatshirt, Vintage Wyoming Crewneck, Moose Mountain"
    ) == "Grand Teton National Park Sweatshirt"
    assert title_keyword_fallback("Retro Frog Shirt | Funny Gift") == "Retro Frog Shirt"
    assert title_keyword_fallback("") == ""


def test_historical_listing_uses_fallback_on_read_without_mutation():
    title = "Grand Teton National Park Sweatshirt, Vintage Wyoming Crewneck"
    metadata = {"keywordSms": [], "keywordSmsStatus": "no_matching_recommendations"}
    listing = _listing_item(_listing(title, metadata), None)
    assert listing["keywordSms"] == ["Grand Teton National Park Sweatshirt"]
    assert listing["keywordSmsQuery"] == "Grand Teton National Park Sweatshirt"
    assert listing["keywordSmsStatus"] == "title_fallback"
    assert metadata["keywordSms"] == []


def test_verified_etsy_keyword_takes_precedence():
    title = "Grand Teton National Park Sweatshirt, Vintage Wyoming Crewneck"
    metadata = {"keywordSms": ["teton park sweatshirt"],
                "keywordSmsQuery": "teton park",
                "keywordSmsStatus": "ready"}
    listing = _listing_item(_listing(title, metadata), None)
    assert listing["keywordSms"] == ["teton park sweatshirt"]
    assert listing["keywordSmsStatus"] == "ready"


def test_sync_persists_fallback_with_provenance_without_etsy_observation():
    title = "Grand Teton National Park Sweatshirt, Vintage Wyoming Crewneck"
    row = {
        "sessionId": "test", "keyword": "Grand Teton", "sourceKind": "search",
        "listingKey": "4564121744", "pageTitle": title,
        "url": "https://www.etsy.com/listing/4564121744/test",
        "searchBadge": "Bestseller",
        "keywordSms": [], "keywordSmsStatus": "no_suggestions",
    }
    _model, _identity, values = _payload_fields("listings", row, "boostgame", "agent")
    metadata = values["metadata_json"]
    assert metadata["keywordSms"] == ["Grand Teton National Park Sweatshirt"]
    assert metadata["keywordSmsStatus"] == "title_fallback"
    assert metadata["keywordSmsCheckedAt"] is None
