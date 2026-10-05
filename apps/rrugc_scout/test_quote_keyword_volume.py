from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from quote_keyword_volume import (
    DEFAULT_PINTEREST_QUERY,
    KeywordScoutHistory,
    _dedupe,
    build_parser,
)


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
