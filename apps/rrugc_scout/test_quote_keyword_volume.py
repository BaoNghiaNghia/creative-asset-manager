from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

import quote_keyword_volume as keyword_scout
from quote_keyword_volume import (
    DEFAULT_PINTEREST_QUERY,
    DEFAULT_RELATED_PER_PIN,
    CAM_QUOTE_REQUEST_TIMEOUT_SECONDS,
    KeywordScoutHistory,
    QuoteScoutClient,
    _dedupe,
    _quote_extract_status_is_terminal,
    build_parser,
)
from scout import PinterestAccessGateError


def test_keyword_scout_uses_fixed_saying_trucker_hat_seed_by_default():
    parser = build_parser()
    args = parser.parse_args([
        "--agent-id",
        "agent-1",
        "--token",
        "secret",
        "--auto-pinterest",
    ])
    assert args.seed_query == DEFAULT_PINTEREST_QUERY == "Saying Trucker hat"
    assert args.profile_dir.endswith("pinterest-profile-keyword")
    assert args.related_per_pin == DEFAULT_RELATED_PER_PIN == 60
    assert args.once is False


def test_quote_dedupe_normalizes_case_whitespace_and_requires_two_words():
    assert _dedupe([
        "  Bad   Day To Be A Hotdog ",
        "bad day to be a hotdog",
        "Another Saying",
        {"text": "slow mornings Club", "confidence": 0.99},
        "{'text': 'PLEASE BE PATIENT WITH ME. I\'M FROM THE 1900s.', 'confidence': 0.99}",
        "HOUSTON",
        "ASTROS",
        "I'm",
        "HOUSTON ASTROS",
        "Morgan Wallen",
    ]) == [
        "Bad Day To Be A Hotdog",
        "Another Saying",
        "slow mornings Club",
        "PLEASE BE PATIENT WITH ME. I'M FROM THE 1900s.",
        "HOUSTON ASTROS",
        "Morgan Wallen",
    ]


def test_keyword_history_is_durable_and_separate_from_review_history():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "keyword-scout-history.json"
        history = KeywordScoutHistory(path)
        history.remember_candidate(
            "https://www.pinterest.com/pin/123/",
            "https://i.pinimg.com/736x/aa/bb/example.jpg",
        )
        history.remember_pending_quotes(["Retry Me Later"])
        history.remember_quotes(["Bad Day To Be A Hotdog"])

        loaded = KeywordScoutHistory(path)
        assert "https://www.pinterest.com/pin/123/" in loaded.seen_pins
        assert loaded.expanded_pins == set()
        assert "aa/bb/example.jpg" in loaded.seen_assets
        assert "bad day to be a hotdog" in loaded.seen_quotes

        loaded.remember_expanded_pin("https://www.pinterest.com/pin/123/?x=1")
        expanded = KeywordScoutHistory(path)
        assert expanded.expanded_pins == {
            "https://www.pinterest.com/pin/123/"
        }
        assert loaded.pending_quotes == ["Retry Me Later"]
        assert "retry me later" in loaded.known_quote_keys

        loaded.remember_quotes(["Retry Me Later"])
        completed = KeywordScoutHistory(path)
        assert completed.pending_quotes == []
        assert "retry me later" in completed.seen_quotes


def test_keyword_volume_skips_http_when_all_keywords_have_fewer_than_two_words(monkeypatch):
    async def fail_post(*_args, **_kwargs):
        raise AssertionError("HTTP request must not run for one-word keywords")

    client = QuoteScoutClient(
        "https://creative-assets.example",
        "agent-1",
        "secret",
    )
    monkeypatch.setattr(client, "_post", fail_post)

    result = asyncio.run(client.resolve_volume(["HOUSTON", "ASTROS", "BASEBALL", "I'm"]))
    assert result == {
        "requested": 0,
        "provider_requested": 0,
        "cached": 0,
        "items": [],
    }
    asyncio.run(client.close())


def test_quote_extract_uses_extended_timeout_for_gemini_failover(monkeypatch):
    captured = {}

    class Candidate:
        pin_url = "https://www.pinterest.com/pin/999/"
        image_url = "https://i.pinimg.com/736x/aa/bb/quote.jpg"
        alt_text = "trucker cap"

    async def fake_post(path, payload, *, operation, timeout_seconds):
        captured["path"] = path
        captured["payload"] = payload
        captured["operation"] = operation
        captured["timeout_seconds"] = timeout_seconds
        return {
            "quotes": ["OUT OF OFFICE"],
            "is_target_cap": True,
            "confidence": 0.99,
            "provider": "gemini",
        }

    client = QuoteScoutClient(
        "https://creative-assets.example",
        "agent-1",
        "secret",
    )
    monkeypatch.setattr(client, "_post", fake_post)

    result = asyncio.run(client.extract_quote(Candidate()))

    assert result["quotes"] == ["OUT OF OFFICE"]
    assert captured["operation"] == "extract_hat_quote"
    assert captured["timeout_seconds"] == CAM_QUOTE_REQUEST_TIMEOUT_SECONDS == 90.0
    asyncio.run(client.close())


def test_keyword_volume_request_includes_pinterest_source(monkeypatch):
    captured = {}

    async def fake_post(path, payload, *, operation):
        captured["path"] = path
        captured["payload"] = payload
        captured["operation"] = operation
        return {"items": []}

    client = QuoteScoutClient(
        "https://creative-assets.example",
        "agent-1",
        "secret",
    )
    monkeypatch.setattr(client, "_post", fake_post)

    asyncio.run(
        client.resolve_volume(
            ["Out of Office"],
            source_image_url="https://i.pinimg.com/736x/aa/bb/source.jpg",
            source_pin_url="https://www.pinterest.com/pin/123/",
        )
    )
    assert captured["operation"] == "resolve_keyword_volume"
    assert captured["payload"] == {
        "keywords": ["Out of Office"],
        "force": False,
        "source_image_url": "https://i.pinimg.com/736x/aa/bb/source.jpg",
        "source_pin_url": "https://www.pinterest.com/pin/123/",
    }
    asyncio.run(client.close())


def test_keyword_volume_request_keeps_all_quotes_from_multi_hat_image(monkeypatch):
    captured = {}

    async def fake_post(path, payload, *, operation):
        captured["path"] = path
        captured["payload"] = payload
        captured["operation"] = operation
        return {"items": []}

    quotes = [
        "BAD DAY TO BE A HOTDOG",
        "OUT OF OFFICE",
        "GIRLS CAN GOLF TOO",
        "WILD AT HEART",
        "NEED MONEY FOR DIRTBIKES",
        "WHY TAKE THE HIGH ROAD",
        "EVERY DREAM BEGINS WITH A WISH",
        "PLEASE BE PATIENT WITH ME",
    ]
    client = QuoteScoutClient(
        "https://creative-assets.example",
        "agent-1",
        "secret",
    )
    monkeypatch.setattr(client, "_post", fake_post)

    asyncio.run(
        client.resolve_volume(
            quotes,
            source_image_url="https://i.pinimg.com/736x/aa/bb/multi-hat.jpg",
            source_pin_url="https://www.pinterest.com/pin/456/",
        )
    )

    assert captured["operation"] == "resolve_keyword_volume"
    assert captured["payload"]["keywords"] == quotes
    assert len(captured["payload"]["keywords"]) == 8
    asyncio.run(client.close())


def test_login_gate_requires_normal_chrome_bootstrap(monkeypatch):
    class FakePage:
        async def wait_for_timeout(self, _ms):
            raise AssertionError("login gate should not wait inside Playwright")

    async def fake_access_gate(_page):
        return "login"

    monkeypatch.setattr(keyword_scout, "access_gate", fake_access_gate)

    async def scenario():
        try:
            await keyword_scout._wait_for_pinterest_access(FakePage())
        except PinterestAccessGateError as exc:
            assert exc.gate == "login"
        else:
            raise AssertionError("login gate did not request bootstrap")

    asyncio.run(scenario())


def test_challenge_gate_can_be_resolved_in_open_browser(monkeypatch):
    calls = iter(["challenge", None])

    class FakePage:
        def __init__(self):
            self.waits = 0

        async def wait_for_timeout(self, _ms):
            self.waits += 1

    async def fake_access_gate(_page):
        return next(calls)

    page = FakePage()
    monkeypatch.setattr(keyword_scout, "access_gate", fake_access_gate)
    asyncio.run(
        keyword_scout._wait_for_pinterest_access(
            page,
            max_seconds=30,
        )
    )
    assert page.waits == 1


def test_quote_extract_http_status_retry_policy_preserves_transient_pins():
    assert _quote_extract_status_is_terminal(400)
    assert _quote_extract_status_is_terminal(413)
    assert _quote_extract_status_is_terminal(422)
    assert not _quote_extract_status_is_terminal(401)
    assert not _quote_extract_status_is_terminal(404)
    assert not _quote_extract_status_is_terminal(429)
    assert not _quote_extract_status_is_terminal(500)
