from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import pytest
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


def test_blocked_keyword_is_not_sent_to_aebrowse():
    from unittest.mock import AsyncMock
    from scout import Candidate
    from quote_keyword_volume import _process_keyword_candidate

    async def exercise():
        with TemporaryDirectory() as directory:
            history = KeywordScoutHistory(Path(directory) / "history.json")
            client = type("Client", (), {})()
            client.extract_quote = AsyncMock(return_value={
                "quotes": ["Cowboy Hat"], "is_target_cap": True,
                "confidence": 0.97, "provider": "mock",
            })
            client.resolve_volume = AsyncMock()
            candidate = Candidate(
                pin_url="https://www.pinterest.com/pin/100/",
                image_url="https://i.pinimg.com/originals/aa.jpg",
            )
            result = await _process_keyword_candidate(
                client, history, candidate, source="root",
                root_pin_url=candidate.pin_url, blocked_keywords={"cowboy hat"},
            )
            assert result.saved_delta == 0
            client.resolve_volume.assert_not_awaited()

    asyncio.run(exercise())


def test_suggested_feedback_remains_hat_context_and_a_minor_discovery_lane():
    assert [cycle for cycle in range(1, 21) if keyword_scout._should_claim_suggested_task(cycle)] == [5, 10, 15, 20]
    assert keyword_scout._suggested_hat_search_query("Houston Astros", 0) == "Houston Astros trucker hat"
    assert keyword_scout._suggested_hat_search_query("Morgan Wallen", 1) == "Morgan Wallen embroidered cap"
    assert keyword_scout._suggested_hat_search_query("", 3) == DEFAULT_PINTEREST_QUERY
    for iteration in range(8):
        search = keyword_scout._suggested_hat_search_query("Wild West", iteration).lower()
        assert "wild west" in search
        assert "hat" in search or "cap" in search


def test_priority_pin_requires_its_own_success_not_unrelated_scans():
    complete = keyword_scout._priority_task_success
    assert complete({"type": "pin"}, processed=30, priority_pin_expanded=False) is False
    assert complete({"type": "pin"}, processed=0, priority_pin_expanded=True) is True
    assert complete({"type": "keyword"}, processed=0, priority_pin_expanded=True) is False
    assert complete({"type": "keyword"}, processed=1, priority_pin_expanded=False) is True


def test_keyword_supervisor_stops_and_reports_after_restart_limit(monkeypatch):
    calls = 0
    reports = []

    async def fake_run(_args):
        nonlocal calls
        calls += 1
        raise keyword_scout.ScoutRestartRequested(
            "keyword_scout_runtime_error_threshold",
            healthy_progress=False,
            last_error_type="RuntimeError",
        )

    async def fake_report(**kwargs):
        reports.append(kwargs)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(keyword_scout, "run_pinterest_quote_scout", fake_run)
    monkeypatch.setattr(keyword_scout, "report_scout_fatal_status", fake_report)
    monkeypatch.setattr(keyword_scout.asyncio, "sleep", no_sleep)
    monkeypatch.setenv("RRUGC_MACHINE_LABEL", "KEYWORD-PC")

    args = type(
        "Args",
        (),
        {
            "base_url": "https://example.test",
            "agent_id": "agent-1",
            "token": "secret",
        },
    )()

    with pytest.raises(keyword_scout.ScoutFatalStop):
        asyncio.run(keyword_scout.supervise_pinterest_quote_scout(args))

    assert calls == keyword_scout.SCOUT_MAX_AUTOMATIC_RESTARTS + 1
    assert len(reports) == 1
    assert reports[0]["error_code"] == "keyword_scout_restart_limit_exceeded"
    assert reports[0]["machine_label"] == "KEYWORD-PC"


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
    assert args.deep_dive_related_per_pin == 150
    assert args.deep_dive_seeds_per_cycle == 3
    assert args.deep_dive_min_search_volume == 1000
    assert args.deep_dive_style_max_depth == 3
    assert args.deep_dive_market_max_depth == 4
    assert args.once is False


def test_keyword_quote_priority_bands_match_clear_hat_text_policy():
    assert keyword_scout._quote_priority_label(0.95) == "high"
    assert keyword_scout._quote_priority_label(
        keyword_scout.KEYWORD_QUOTE_HIGH_PRIORITY_SCORE
    ) == "high"
    assert keyword_scout._quote_priority_label(0.70) == "normal"
    assert keyword_scout._quote_priority_label(
        keyword_scout.KEYWORD_QUOTE_MIN_PRIORITY_SCORE
    ) == "normal"
    assert keyword_scout._quote_priority_label(0.59) == "low"
    assert keyword_scout._quote_priority_score({"confidence": 1.4}) == 1.0
    assert keyword_scout._quote_priority_score({"confidence": -0.2}) == 0.0


def test_low_clarity_hat_quote_skips_aebrowse_volume():
    class Candidate:
        pin_url = "https://www.pinterest.com/pin/clear-priority-test/"
        image_url = "https://i.pinimg.com/736x/aa/bb/low-clarity.jpg"
        alt_text = "embroidered saying cap"

    class FakeClient:
        volume_called = False

        async def extract_quote(self, _candidate):
            return {
                "quotes": ["Thinking about not thinking"],
                "is_target_cap": True,
                "confidence": 0.42,
                "provider": "gemini",
                "model": "gemini-test",
            }

        async def resolve_volume(self, *_args, **_kwargs):
            self.volume_called = True
            raise AssertionError("low-clarity quote must not call AEBrowse")

    with TemporaryDirectory() as directory:
        history = KeywordScoutHistory(
            Path(directory) / "keyword-scout-history.json"
        )
        client = FakeClient()
        result = asyncio.run(
            keyword_scout._process_keyword_candidate(
                client,
                history,
                Candidate(),
                source="root",
                root_pin_url=Candidate.pin_url,
            )
        )

        assert result.quote_delta == 0
        assert result.saved_delta == 0
        assert result.priority_score == 0.42
        assert result.market_opportunity is False
        assert client.volume_called is False
        assert Candidate.pin_url in history.seen_pins


def test_high_volume_low_competition_becomes_market_deep_dive():
    class Candidate:
        pin_url = "https://www.pinterest.com/pin/market-opportunity/"
        image_url = "https://i.pinimg.com/736x/aa/bb/market.jpg"
        alt_text = "clear saying cap"

    class FakeClient:
        async def extract_quote(self, _candidate):
            return {
                "quotes": ["Wanna Be My Cardio"],
                "is_target_cap": True,
                "confidence": 0.91,
                "provider": "gemini",
                "model": "gemini-test",
            }

        async def resolve_volume(self, *_args, **_kwargs):
            return {
                "items": [
                    {
                        "keyword": "Wanna Be My Cardio",
                        "search_volume": 5400,
                        "competition": "LOW",
                    }
                ],
                "provider_requested": 1,
                "cached": 0,
            }

    with TemporaryDirectory() as directory:
        history = KeywordScoutHistory(
            Path(directory) / "keyword-scout-history.json"
        )
        result = asyncio.run(
            keyword_scout._process_keyword_candidate(
                FakeClient(),
                history,
                Candidate(),
                source="root",
                root_pin_url=Candidate.pin_url,
                deep_dive_min_search_volume=1000,
            )
        )

    assert result.saved_delta == 1
    assert result.max_search_volume == 5400
    assert result.low_competition_search_volume == 5400
    assert result.market_opportunity is True
    assert keyword_scout._deep_dive_max_depth(
        result,
        style_max_depth=3,
        market_max_depth=4,
    ) == 4


def test_adaptive_market_score_allows_high_volume_high_competition():
    score, picked = keyword_scout._market_opportunity_score(
        [
            {
                "keyword": "GIRL DAD",
                "search_volume": 12100,
                "competition": "HIGH",
                "picked": False,
            }
        ],
        priority_score=0.96,
    )
    assert picked is False
    assert score >= keyword_scout.MARKET_DEEP_DIVE_SCORE_THRESHOLD


def test_market_score_rewards_picked_keywords_without_forcing_weak_rows():
    picked_score, picked = keyword_scout._market_opportunity_score(
        [
            {
                "keyword": "NO FRIENDS JUST FAMILIA",
                "search_volume": 40,
                "competition": "LOW",
                "picked": True,
            }
        ],
        priority_score=0.95,
    )
    weak_score, weak_picked = keyword_scout._market_opportunity_score(
        [
            {
                "keyword": "random phrase",
                "search_volume": 10,
                "competition": "HIGH",
                "picked": False,
            }
        ],
        priority_score=0.61,
    )
    assert picked is True
    assert picked_score > weak_score
    assert weak_picked is False
    assert weak_score < keyword_scout.MARKET_DEEP_DIVE_SCORE_THRESHOLD


def test_keyword_metadata_prefilter_only_rejects_explicit_non_hat_products():
    assert keyword_scout._keyword_metadata_prefilter_reason(
        keyword_scout.Candidate(
            "https://www.pinterest.com/pin/hat/",
            "https://i.pinimg.com/736x/hat.jpg",
            alt_text="embroidered trucker cap saying",
        )
    ) is None
    assert keyword_scout._keyword_metadata_prefilter_reason(
        keyword_scout.Candidate(
            "https://www.pinterest.com/pin/shirt/",
            "https://i.pinimg.com/736x/shirt.jpg",
            alt_text="funny saying printed t-shirt",
        )
    ) == "explicit_non_hat_product:t-shirt"
    assert keyword_scout._keyword_metadata_prefilter_reason(
        keyword_scout.Candidate(
            "https://www.pinterest.com/pin/unknown/",
            "https://i.pinimg.com/736x/unknown.jpg",
            alt_text="funny quote gift idea",
        )
    ) is None


def test_keyword_metadata_prefilter_rejects_more_explicit_non_hat_and_digital_products():
    assert keyword_scout._keyword_metadata_prefilter_reason(
        keyword_scout.Candidate(
            "https://www.pinterest.com/pin/tumbler/",
            "https://i.pinimg.com/736x/tumbler.jpg",
            alt_text="funny quote insulated water bottle gift",
        )
    ) == "explicit_non_hat_product:water bottle"
    assert keyword_scout._keyword_metadata_prefilter_reason(
        keyword_scout.Candidate(
            "https://www.pinterest.com/pin/svg/",
            "https://i.pinimg.com/736x/svg.jpg",
            context_text="western saying SVG file instant download",
        )
    ) == "explicit_digital_product:instant download"
    assert keyword_scout._keyword_metadata_prefilter_reason(
        keyword_scout.Candidate(
            "https://www.pinterest.com/pin/hat-with-shirt-context/",
            "https://i.pinimg.com/736x/hat.jpg",
            alt_text="embroidered trucker hat",
            context_text="styled with a graphic shirt",
        )
    ) is None


def test_deep_dive_prefers_market_before_style_only_and_caps_depth():
    style = keyword_scout.KeywordCandidateResult(
        priority_score=0.99,
        max_search_volume=20000,
        market_opportunity=False,
    )
    market = keyword_scout.KeywordCandidateResult(
        priority_score=0.86,
        max_search_volume=1200,
        low_competition_search_volume=1200,
        market_opportunity=True,
    )
    style_seed = keyword_scout.DeepDiveSeed(
        keyword_scout.Candidate(
            "https://www.pinterest.com/pin/style/",
            "https://i.pinimg.com/736x/style.jpg",
        ),
        "https://www.pinterest.com/pin/root/",
        1,
        style,
    )
    market_seed = keyword_scout.DeepDiveSeed(
        keyword_scout.Candidate(
            "https://www.pinterest.com/pin/market/",
            "https://i.pinimg.com/736x/market.jpg",
        ),
        "https://www.pinterest.com/pin/root/",
        1,
        market,
    )
    seeds = [style_seed, market_seed]
    seeds.sort(key=keyword_scout._deep_dive_sort_key, reverse=True)

    assert seeds[0] is market_seed
    assert keyword_scout._deep_dive_max_depth(
        style,
        style_max_depth=3,
        market_max_depth=4,
    ) == 3
    assert keyword_scout._deep_dive_max_depth(
        market,
        style_max_depth=3,
        market_max_depth=4,
    ) == 4


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
        assert expanded.deep_expanded_pins == set()
        expanded.remember_deep_expanded_pin(
            "https://www.pinterest.com/pin/123/?deep=1"
        )
        deep_expanded = KeywordScoutHistory(path)
        assert deep_expanded.deep_expanded_pins == {
            "https://www.pinterest.com/pin/123/"
        }
        assert loaded.pending_quotes == ["Retry Me Later"]
        assert "retry me later" in loaded.known_quote_keys

        loaded.remember_quotes(["Retry Me Later"])
        completed = KeywordScoutHistory(path)
        assert completed.pending_quotes == []
        assert "retry me later" in completed.seen_quotes


def test_partial_volume_response_keeps_missing_keywords_for_retry():
    verified, pending = keyword_scout._partition_volume_result(
        ["Houston Astros", "Morgan Wallen", "Blank Result"],
        {
            "items": [
                {"keyword": "Houston Astros", "provider": "aebrowse_google_ads", "search_volume": 0},
                {"keyword": "Morgan Wallen", "provider": "pending", "search_volume": 0},
            ]
        },
    )
    assert verified == ["Houston Astros"]
    assert pending == ["Morgan Wallen", "Blank Result"]


def test_keyword_scout_partial_volume_is_queued_not_marked_seen(tmp_path):
    class TestCandidate:
        pin_url = "https://www.pinterest.com/pin/999/"
        image_url = "https://i.pinimg.com/736x/aa/bb/cc.jpg"
        alt_text = "hat"

    class FakeClient:
        async def extract_quote(self, candidate):
            return {
                "quotes": ["Morgan Wallen"],
                "is_target_cap": True,
                "confidence": 0.95,
            }

        async def resolve_volume(self, *args, **kwargs):
            return {
                "items": [
                    {"keyword": "Morgan Wallen", "search_volume": 0, "provider": "pending"}
                ],
                "provider_requested": 1,
            }

    history = KeywordScoutHistory(tmp_path / "pending-test-history.json")
    result = asyncio.run(keyword_scout._process_keyword_candidate(
        FakeClient(),
        history,
        TestCandidate(),
        source="root",
        root_pin_url=TestCandidate.pin_url,
    ))
    assert result.quote_delta == 1
    assert result.saved_delta == 0
    assert history.pending_quotes == ["Morgan Wallen"]
    assert "morgan wallen" not in history.seen_quotes


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


def test_keyword_gemini_backpressure_stops_cycle_without_losing_pin(tmp_path):
    class Candidate:
        pin_url = "https://www.pinterest.com/pin/551/"
        image_url = "https://i.pinimg.com/736x/aa/bb/cap.jpg"
        alt_text = "cap"

    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(
            503,
            headers={"Retry-After": "240"},
            json={"detail": {
                "code": "rrugc_analysis_backpressure",
                "message": "Shared AI backlog",
            }},
        )

    async def scenario():
        history = KeywordScoutHistory(tmp_path / "keyword-history.json")
        client = QuoteScoutClient("https://cam.example", "agent-1", "unit-test")
        await client.client.aclose()
        client.client = httpx.AsyncClient(
            base_url="https://cam.example",
            transport=httpx.MockTransport(handler),
        )
        try:
            with pytest.raises(keyword_scout.KeywordScoutCapacityPaused) as exc:
                await keyword_scout._process_keyword_candidate(
                    client, history, Candidate(),
                    source="root", root_pin_url=Candidate.pin_url,
                )
            assert exc.value.retry_seconds == 240
            assert Candidate.pin_url not in history.seen_pins
        finally:
            await client.close()

    asyncio.run(scenario())
    assert len(requests) == 1


def test_keyword_capacity_preflight_scans_with_reduced_budget_when_backlogged():
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "analysis_backpressure": {
                    "active": True,
                    "pending_jobs": 245,
                    "oldest_wait_seconds": 1800,
                }
            },
        )

    async def scenario():
        client = QuoteScoutClient("https://cam.example", "agent-1", "unit-test")
        await client.client.aclose()
        client.client = httpx.AsyncClient(
            base_url="https://cam.example",
            transport=httpx.MockTransport(handler),
        )
        try:
            assert await client.ensure_analysis_capacity() is True
        finally:
            await client.close()

    asyncio.run(scenario())
    assert requests == [
        "/api/v1/realistic-review-ugc/scout-agents/agent-1/diagnostics"
    ]


def test_keyword_capacity_preflight_uses_keyword_fair_share_not_review_backlog():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={
                "analysis_backpressure": {
                    "active": True, "pending_jobs": 430,
                    "oldest_wait_seconds": 3600,
                },
                "keyword_quote_backpressure": {
                    "active": False, "retry_seconds": 0,
                    "reason": "keyword_fair_share_allowed",
                },
            },
        )

    async def scenario():
        client = QuoteScoutClient("https://cam.example", "agent-1", "test")
        await client.client.aclose()
        client.client = httpx.AsyncClient(
            base_url="https://cam.example",
            transport=httpx.MockTransport(handler),
        )
        try:
            await client.ensure_analysis_capacity()
        finally:
            await client.close()

    asyncio.run(scenario())


def test_keyword_capacity_preflight_continues_scanning_during_fair_share_wait():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={
                "analysis_backpressure": {"active": True, "pending_jobs": 430,
                                           "oldest_wait_seconds": 3600},
                "keyword_quote_backpressure": {
                    "active": True, "retry_seconds": 58,
                    "reason": "keyword_fair_share_wait",
                },
            },
        )

    async def scenario():
        client = QuoteScoutClient("https://cam.example", "agent-1", "test")
        await client.client.aclose()
        client.client = httpx.AsyncClient(
            base_url="https://cam.example",
            transport=httpx.MockTransport(handler),
        )
        try:
            assert await client.ensure_analysis_capacity() is True
        finally:
            await client.close()

    asyncio.run(scenario())


def test_keyword_capacity_preflight_is_fail_open_for_transient_diagnostics_errors():
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(502, json={"detail": "temporary proxy error"})

    async def scenario():
        client = QuoteScoutClient("https://cam.example", "agent-1", "unit-test")
        await client.client.aclose()
        client.client = httpx.AsyncClient(
            base_url="https://cam.example",
            transport=httpx.MockTransport(handler),
        )
        try:
            await client.ensure_analysis_capacity()
        finally:
            await client.close()

    asyncio.run(scenario())
    assert calls == 1


def test_keyword_capacity_preflight_allows_work_when_pressure_is_clear():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "analysis_backpressure": {
                    "active": False,
                    "pending_jobs": 12,
                    "oldest_wait_seconds": 90,
                }
            },
        )

    async def scenario():
        client = QuoteScoutClient("https://cam.example", "agent-1", "unit-test")
        await client.client.aclose()
        client.client = httpx.AsyncClient(
            base_url="https://cam.example",
            transport=httpx.MockTransport(handler),
        )
        try:
            await client.ensure_analysis_capacity()
        finally:
            await client.close()

    asyncio.run(scenario())


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


def test_keyword_search_uses_commit_navigation_with_pin_readiness_check():
    import inspect

    source = inspect.getsource(keyword_scout.run_pinterest_quote_scout)
    assert 'wait_until="commit"' in source
    assert "await wait_for_pin_growth(" in source
    assert "await _wait_for_pinterest_access(page)" in source


def test_quote_extract_http_status_retry_policy_preserves_transient_pins():
    assert _quote_extract_status_is_terminal(400)
    assert _quote_extract_status_is_terminal(413)
    assert not _quote_extract_status_is_terminal(422)
    assert not _quote_extract_status_is_terminal(422, "gemini_http_error")
    assert _quote_extract_status_is_terminal(422, "quote_scout_image_type_rejected")
    assert not _quote_extract_status_is_terminal(401)
    assert not _quote_extract_status_is_terminal(404)
    assert not _quote_extract_status_is_terminal(429)
    assert not _quote_extract_status_is_terminal(500)


def test_gemini_http_422_skips_one_pin_without_pausing_entire_keyword_scout():
    class Candidate:
        pin_url = "https://www.pinterest.com/pin/gemini-422/"
        image_url = "https://i.pinimg.com/736x/aa/bb/quote.jpg"
        alt_text = "trucker cap"

    class Client:
        async def extract_quote(self, _candidate):
            request = httpx.Request("POST", "https://creative-assets.example/quote")
            response = httpx.Response(
                422,
                request=request,
                json={"detail": {"code": "gemini_http_error"}},
            )
            raise httpx.HTTPStatusError(
                "Gemini rejected the request", request=request, response=response
            )

    with TemporaryDirectory() as directory:
        history = KeywordScoutHistory(Path(directory) / "keyword-scout-history.json")
        result = asyncio.run(
            keyword_scout._process_keyword_candidate(
                Client(), history, Candidate(),
                source="root", root_pin_url=Candidate.pin_url,
            )
        )
        assert result.saved_delta == 0
        assert Candidate.pin_url not in history.seen_pins


def test_quote_extract_preserves_final_http_status_after_retries(monkeypatch):
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            503,
            request=request,
            json={
                "detail": {
                    "code": "gemini_model_pool_temporarily_unavailable",
                    "message": "No Gemini model is currently available.",
                }
            },
        )

    async def no_sleep(_seconds):
        return None

    class Candidate:
        pin_url = "https://www.pinterest.com/pin/999/"
        image_url = "https://i.pinimg.com/736x/aa/bb/quote.jpg"
        alt_text = "trucker cap"

    async def scenario():
        client = QuoteScoutClient(
            "https://creative-assets.example",
            "agent-1",
            "secret",
        )
        await client.client.aclose()
        client.client = httpx.AsyncClient(
            base_url="https://creative-assets.example",
            headers={"Authorization": "Bearer secret"},
            transport=httpx.MockTransport(handler),
        )
        try:
            await client.extract_quote(Candidate())
        except httpx.HTTPStatusError as exc:
            assert exc.response.status_code == 503
        else:
            raise AssertionError("503 was masked instead of being preserved")
        finally:
            await client.close()

    monkeypatch.setattr(keyword_scout.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(keyword_scout.random, "random", lambda: 0.0)
    asyncio.run(scenario())
    assert attempts == 3

def test_fair_share_retry_after_below_30_seconds_is_honored():
    assert keyword_scout.KeywordScoutCapacityPaused(20).retry_seconds == 20
    assert keyword_scout.KeywordScoutCapacityPaused(6).retry_seconds == 6
    assert keyword_scout.KeywordScoutCapacityPaused(1).retry_seconds == 5
    assert keyword_scout.KeywordScoutCapacityPaused(999).retry_seconds == 900


def test_fair_share_retries_same_pin_and_refreshes_lease(monkeypatch):
    calls = {"process": 0, "refresh": 0, "waits": []}
    async def fake_sleep(seconds):
        calls["waits"].append(seconds)
    monkeypatch.setattr(keyword_scout.asyncio, "sleep", fake_sleep)

    expected = object()
    async def process():
        calls["process"] += 1
        if calls["process"] < 3:
            raise keyword_scout.KeywordScoutCapacityPaused(56)
        return expected
    async def renew():
        calls["refresh"] += 1
    actual = asyncio.run(keyword_scout.process_keyword_candidate_with_fair_share(
        process, refresh_lease=renew, source="related",
        root_pin_url="https://www.pinterest.com/pin/123/",
        retry_limit=3,
    ))
    assert actual is expected
    assert calls["process"] == 3
    assert calls["waits"] == [56, 56]
    assert calls["refresh"] >= 3


def test_fair_share_retry_limit_preserves_unprocessed_pin(monkeypatch):
    async def fake_sleep(_seconds):
        return None
    monkeypatch.setattr(keyword_scout.asyncio, "sleep", fake_sleep)
    calls = 0
    async def process():
        nonlocal calls
        calls += 1
        raise keyword_scout.KeywordScoutCapacityPaused(60)
    async def refresh():
        return None
    with pytest.raises(keyword_scout.KeywordScoutCapacityPaused):
        asyncio.run(keyword_scout.process_keyword_candidate_with_fair_share(
            process, refresh_lease=refresh, source="root",
            root_pin_url="https://www.pinterest.com/pin/456/",
            retry_limit=2,
        ))
    assert calls == 3


def test_pressured_scan_budget_is_bounded_without_changing_default_search_depth():
    assert keyword_scout.DEFAULT_RELATED_PER_PIN == 60
    assert keyword_scout.DEFAULT_DEEP_DIVE_RELATED_PER_PIN == 150
    assert keyword_scout.PRESSURED_ROOT_DETAILS_PER_CYCLE <= 4
    assert keyword_scout.PRESSURED_RELATED_DETAILS_PER_ROOT <= 8
    assert keyword_scout.PRESSURED_FAIR_SHARE_RETRIES >= 2

def test_ocr_edge_clipping_merges_only_near_identical_phrases_from_one_pin():
    merge = keyword_scout._merge_ocr_fragment_variants
    assert merge([
        "AM A STAR BECAUSE I JUST AM", "I AM A STAR BECAUSE I JUST AM",
        "NOT A STAR BECAUSE I JUST AM", "ACTUALLY THIS IS MY FIRST RODEO",
    ]) == [
        "I AM A STAR BECAUSE I JUST AM",
        "NOT A STAR BECAUSE I JUST AM",
        "ACTUALLY THIS IS MY FIRST RODEO",
    ]
    # Distinct complete 2-4 word sayings remain separate, including multi-hat Pins.
    assert merge(["GAME DAY", "MY GAME DAY", "LOVE MORE", "LOVE LESS"]) == [
        "GAME DAY", "MY GAME DAY", "LOVE MORE", "LOVE LESS",
    ]
    # Same phrase in a different Pin is intentionally handled by durable history.