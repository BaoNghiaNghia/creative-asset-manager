from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

import quote_keyword_volume as keyword_scout
from quote_keyword_volume import (
    DEFAULT_PINTEREST_QUERY,
    KeywordScoutHistory,
    _dedupe,
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
    assert args.seed_query == DEFAULT_PINTEREST_QUERY == "saying trucker hat"
    assert args.profile_dir.endswith("pinterest-profile-keyword")


def test_quote_dedupe_normalizes_case_and_whitespace():
    assert _dedupe([
        "  Bad   Day To Be A Hotdog ",
        "bad day to be a hotdog",
        "Another Saying",
    ]) == [
        "Bad Day To Be A Hotdog",
        "Another Saying",
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
        assert "aa/bb/example.jpg" in loaded.seen_assets
        assert "bad day to be a hotdog" in loaded.seen_quotes
        assert loaded.pending_quotes == ["Retry Me Later"]
        assert "retry me later" in loaded.known_quote_keys

        loaded.remember_quotes(["Retry Me Later"])
        completed = KeywordScoutHistory(path)
        assert completed.pending_quotes == []
        assert "retry me later" in completed.seen_quotes


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
