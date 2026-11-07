import importlib.util
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("research_auto_discover", HERE / "auto_discover.py")
import sys
sys.path.insert(0, str(HERE))
auto = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auto)


class FakeSearch:
    def __init__(self):
        self.calls = []

    def search(self, query):
        self.calls.append(query)
        if "etsy.com" in query:
            text = "Title: MADE TO WORSHIP embroidered trucker hat\nURL: https://www.etsy.com/listing/123456789/custom-cap?utm=bad"
        elif "amazon.com" in query:
            text = "Title: Funny baseball cap stitched words\nURL: https://www.amazon.com/dp/B012345678?tag=bad"
        else:
            text = "Title: Embroidered saying trucker hat\nURL: https://www.pinterest.com/pin/987654321/?pin=bad"
        return [{"type": "text", "text": text}]


class AutoDiscoverTests(unittest.TestCase):

    def test_adaptive_query_ranking_learns_without_new_provider_calls(self):
        with tempfile.TemporaryDirectory() as folder:
            with auto.init_db(Path(folder) / "state.sqlite") as db:
                auto.prepare_state(db)
                self.assertEqual(auto.select_queries(db, set(), 2, 0), [0, 1])
                for _ in range(5):
                    auto.update_query_metrics(db, 0, 0, 8)
                for _ in range(3):
                    auto.update_query_metrics(db, 3, 5, 0)
                selected = auto.select_queries(db, set(), 2, 0)
                self.assertEqual(selected[0], 3)
                self.assertNotEqual(auto.SEARCH_QUERIES[selected[0]][0],
                                    auto.SEARCH_QUERIES[selected[1]][0])
                self.assertNotIn(0, auto.select_queries(db, {0, 3}, 2, 0))
                self.assertLessEqual(db.execute(
                    "SELECT COUNT(*) FROM discovery_query_metrics").fetchone()[0],
                    len(auto.SEARCH_QUERIES))

    def test_playwright_verification_is_opt_in_bounded_and_preserves_outbox(self):
        with tempfile.TemporaryDirectory() as folder:
            with auto.init_db(Path(folder) / "state.sqlite") as db:
                auto.prepare_state(db)
                items = [
                    {"source": "etsy", "channel": "auto-discover",
                     "title": "Custom Name Embroidered Trucker Hat",
                     "url": f"https://www.etsy.com/listing/{123456700 + n}",
                     "excerpt": "Unverified public search result"} for n in range(6)
                ]
                auto.ingest(db, items)
                with patch.dict(os.environ, {"CAM_RESEARCH_PLAYWRIGHT_VERIFY": "0"}):
                    with patch.object(auto, "playwright_page_title") as verifier:
                        self.assertEqual(auto.verify_selected_titles(db, items), 0)
                        verifier.assert_not_called()
                with patch.dict(os.environ, {"CAM_RESEARCH_PLAYWRIGHT_VERIFY": "1"}):
                    with patch.object(auto, "playwright_page_title",
                                      return_value="Original Custom Trucker Hat") as verifier:
                        self.assertEqual(auto.verify_selected_titles(db, items), 1)
                        self.assertEqual(auto.verify_selected_titles(db, items), 1)
                        self.assertEqual(auto.verify_selected_titles(db, items), 1)
                        self.assertEqual(auto.verify_selected_titles(db, items), 1)
                        self.assertEqual(auto.verify_selected_titles(db, items), 0)
                        self.assertEqual(verifier.call_count, 4)
                self.assertEqual(auto.status_dict(db)["browser_title_checks_today"], 4)
                self.assertEqual(db.execute(
                    "SELECT COUNT(*) FROM research_signal").fetchone()[0], 6)
                self.assertEqual(db.execute(
                    "SELECT COUNT(*) FROM research_signal WHERE status='pending'"
                ).fetchone()[0], 6)

    def test_browser_failure_is_recorded_once_without_dropping_signal(self):
        item = {"source": "etsy", "channel": "auto-discover",
                "title": "Custom Name Embroidered Trucker Hat",
                "url": "https://www.etsy.com/listing/123456789",
                "excerpt": "Unverified search result"}
        with tempfile.TemporaryDirectory() as folder:
            with auto.init_db(Path(folder) / "state.sqlite") as db:
                auto.prepare_state(db)
                auto.ingest(db, [item])
                with patch.dict(os.environ, {"CAM_RESEARCH_PLAYWRIGHT_VERIFY": "1"}):
                    with patch.object(auto, "playwright_page_title", return_value=None) as ver:
                        self.assertEqual(auto.verify_selected_titles(db, [item]), 0)
                        self.assertEqual(auto.verify_selected_titles(db, [item]), 0)
                        ver.assert_called_once()
                self.assertEqual(db.execute(
                    "SELECT status FROM discovery_browser_checks").fetchone()[0],
                    "unavailable")
                self.assertEqual(db.execute(
                    "SELECT status FROM research_signal").fetchone()[0], "pending")
    def test_parser_rejects_cross_source_domains_search_paths_and_spoofs(self):
        body = [{"type": "text", "text": (
            "Title: Cap search\nURL: https://www.etsy.com/search?q=cap\n"
            "Title: Cap spoof\nURL: https://www.etsy.com.evil.test/listing/1234567\n"
            "Title: Cap mismatched\nURL: https://www.amazon.com/dp/B012345678\n"
            "Title: Embroidered trucker hat\nURL: https://www.etsy.com/listing/123456789/title?utm_campaign=x"
        )}]
        items = auto.parse_marketplace_results("etsy", body, "embroidered cap")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://www.etsy.com/listing/123456789")
        self.assertEqual(items[0]["channel"], "auto-discover")

    @patch.object(auto, "service_gate", return_value=True)
    @patch.object(auto, "heartbeat")
    def test_cycle_runs_without_windows_and_uses_durable_outbox(self, heartbeat, service_gate):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "research.sqlite3"
            mock = FakeSearch()
            with auto.init_db(path) as db:
                result = auto.run_once(db, search_provider=mock, max_queries=2, daily_limit=2)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["queries_today"], 2)
                self.assertEqual(result["pending_outbox"], 2)
                again = auto.run_once(db, search_provider=mock, max_queries=2, daily_limit=2)
                self.assertEqual(again["status"], "rate_budget_exhausted")
                self.assertEqual(again["pending_outbox"], 2)
                self.assertEqual(len(mock.calls), 2)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM research_signal").fetchone()[0], 2)
            self.assertEqual(heartbeat.call_args_list[0].args[0], "busy")
            self.assertEqual(heartbeat.call_args_list[1].args[0], "ready")

    @patch.object(auto, "service_gate", return_value=False)
    @patch.object(auto, "heartbeat")
    def test_operator_pause_never_searches_or_sends_aebrowse(self, heartbeat, service_gate):
        with tempfile.TemporaryDirectory() as folder:
            mock = FakeSearch()
            with auto.init_db(Path(folder) / "state.sqlite") as db:
                result = auto.run_once(db, search_provider=mock)
                self.assertEqual(result["status"], "paused")
                self.assertEqual(result["queries_today"], 0)
                self.assertEqual(mock.calls, [])
                heartbeat.assert_not_called()

    @patch.object(auto, "service_gate", return_value=True)
    @patch.object(auto, "heartbeat")
    def test_exa_failure_preserves_queue_and_observes_daily_budget(self, heartbeat, service_gate):
        class Failure:
            def search(self, query):
                raise RuntimeError("exa_rate_limited")
        with tempfile.TemporaryDirectory() as folder:
            with auto.init_db(Path(folder) / "state.sqlite") as db:
                result = auto.run_once(db, search_provider=Failure(), daily_limit=2)
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["queries_today"], 1)
                self.assertEqual(result["last_error"], "exa_rate_limited")
                self.assertEqual(result["pending_outbox"], 0)
                cooled = auto.run_once(db, search_provider=Failure(), daily_limit=2)
                self.assertEqual(cooled["status"], "provider_cooldown")
                self.assertEqual(cooled["queries_today"], 1)

    @patch.object(auto, "service_gate", return_value=True)
    @patch.object(auto, "heartbeat")
    def test_outbox_sync_during_provider_cooldown_or_exhausted_budget(self, heartbeat, gate):
        paired = {"CAM_RESEARCH_SCOUT_AGENT_ID": "12345678-1234-1234-1234-123456789abc",
                  "CAM_RESEARCH_SCOUT_TOKEN": "test-only-token-with-minimum-length"}
        for mode in ("cooldown", "budget"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                with auto.init_db(Path(folder) / "state.sqlite") as db:
                    auto.prepare_state(db)
                    auto.ingest(db, [{"source": "etsy", "channel": "auto-discover",
                                      "title": "Trucker hat quote",
                                      "url": "https://www.etsy.com/listing/123456789",
                                      "excerpt": "Public search result"}])
                    if mode == "cooldown":
                        auto.save_meta(db, cooldown_until=(datetime.now(timezone.utc)
                                      + timedelta(hours=2)).isoformat())

                    def acknowledge(_db):
                        _db.execute("UPDATE research_signal SET status='synced' WHERE status='pending'")
                        _db.commit()
                        return {"status": "idle", "sent": 1}

                    with patch.dict(os.environ, paired), patch.object(auto, "sync_to_cam",
                         side_effect=acknowledge) as sync:
                        result = auto.run_once(db, search_provider=FakeSearch(), max_queries=0)
                    self.assertEqual(result["status"], "provider_cooldown" if
                                     mode == "cooldown" else "rate_budget_exhausted")
                    self.assertEqual(result["pending_outbox"], 0)
                    self.assertEqual(sync.call_count, 1)
                    self.assertEqual(result["queries_today"], 0)

    @patch.object(auto, "service_gate", return_value=True)
    @patch.object(auto, "heartbeat")
    def test_sync_failure_in_cooldown_preserves_pending_signal(self, heartbeat, gate):
        paired = {"CAM_RESEARCH_SCOUT_AGENT_ID": "12345678-1234-1234-1234-123456789abc",
                  "CAM_RESEARCH_SCOUT_TOKEN": "test-only-token-with-minimum-length"}
        with tempfile.TemporaryDirectory() as folder:
            with auto.init_db(Path(folder) / "state.sqlite") as db:
                auto.prepare_state(db)
                auto.ingest(db, [{"source": "etsy", "channel": "auto-discover",
                                  "title": "Trucker hat quote",
                                  "url": "https://www.etsy.com/listing/123456789",
                                  "excerpt": "Public search result"}])
                auto.save_meta(db, cooldown_until=(datetime.now(timezone.utc)
                              + timedelta(hours=2)).isoformat())
                with patch.dict(os.environ, paired), patch.object(
                        auto, "sync_to_cam", side_effect=ValueError("rejected")) as sync:
                    result = auto.run_once(db, search_provider=FakeSearch())
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["last_error"], "cam_sync_retryable")
                self.assertEqual(result["pending_outbox"], 1)
                sync.assert_called_once()
                heartbeat.assert_called_with("error", "cam_sync_retryable")

    def test_tavily_keyless_official_endpoint_and_marketplace_filtering(self):
        class Response:
            status_code = 200
            headers = {"content-type": "application/json"}
            content = b'{}'
            def json(self):
                return {"results": [
                    {"title": "Embroidered trucker hat funny saying",
                     "url": "https://www.etsy.com/listing/1828424924/cap?ref=search",
                     "content": "Stitched quote on a cap"},
                    {"title": "Category (not a listing)",
                     "url": "https://www.etsy.com/market/trucker_hats",
                     "content": "Hats"},
                    {"title": "Untrusted third-party item",
                     "url": "https://evil.example/listing/12345",
                     "content": "Cap"},
                ]}
        client = auto.TavilyKeylessSearch()
        calls = []
        def post(url, **kwargs):
            calls.append((url, kwargs))
            return Response()
        client.http.post = post
        blocks = client.search("site:etsy.com/listing funny saying embroidered trucker hat")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], auto.TAVILY_ENDPOINT)
        self.assertEqual(calls[0][1]["headers"]["X-Tavily-Access-Mode"], "keyless")
        self.assertEqual(calls[0][1]["json"]["include_domains"], ["etsy.com"])
        self.assertNotIn("api_key", calls[0][1]["json"])
        self.assertFalse(calls[0][1]["allow_redirects"])
        items = auto.parse_marketplace_results("etsy", blocks, "cap", auto.TAVILY_PROVIDER)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://www.etsy.com/listing/1828424924")
        self.assertIn("tavily-keyless", items[0]["excerpt"])
        with self.assertRaisesRegex(ValueError, "tavily_source_invalid"):
            client.search("other random search")

    @patch.object(auto, "service_gate", return_value=True)
    @patch.object(auto, "heartbeat")
    def test_keyless_results_enter_durable_outbox_without_secret(self, heartbeat, gate):
        class FakeTavily:
            provider = auto.TAVILY_PROVIDER
            def search(self, query):
                return FakeSearch().search(query)
        with tempfile.TemporaryDirectory() as folder:
            with auto.init_db(Path(folder) / "state.sqlite") as db:
                auto.prepare_state(db)
                with patch.dict(os.environ, {"CAM_RESEARCH_SEARCH_PROVIDER": "auto",
                                             "CAM_RESEARCH_BRAVE_API_KEY": "",
                                             "CAM_RESEARCH_EXA_API_KEY": ""}):
                    self.assertIsInstance(auto.configured_search(db), auto.TavilyKeylessSearch)
                result = auto.run_once(db, search_provider=FakeTavily(), max_queries=2,
                                       daily_limit=20)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["provider"], auto.TAVILY_PROVIDER)
                self.assertEqual(result["pending_outbox"], 2)
                self.assertEqual(result["queries_today"], 2)

    @patch.object(auto, "service_gate", return_value=True)
    @patch.object(auto, "heartbeat")
    def test_tavily_429_is_independent_and_does_not_repeat_or_drop_data(self, heartbeat, gate):
        class Limited:
            provider = auto.TAVILY_PROVIDER
            def search(self, query):
                raise RuntimeError("tavily_rate_limited")
        with tempfile.TemporaryDirectory() as folder:
            with auto.init_db(Path(folder) / "state.sqlite") as db:
                auto.prepare_state(db)
                auto.ingest(db, [{"source": "etsy", "channel": "auto-discover",
                                  "title": "Trucker hat quote",
                                  "url": "https://www.etsy.com/listing/123456789",
                                  "excerpt": "Search result"}])
                result = auto.run_once(db, search_provider=Limited())
                self.assertEqual(result["status"], "error")
                self.assertTrue(auto._future_cooldown(db, "tavily_cooldown_until"))
                self.assertEqual(result["pending_outbox"], 1)
                again = auto.run_once(db, search_provider=Limited())
                self.assertEqual(again["status"], "provider_cooldown")
                self.assertEqual(again["queries_today"], 1)
                self.assertEqual(again["pending_outbox"], 1)

    def test_tavily_parses_real_style_amazon_product_url_without_category_pages(self):
        blocks = [{"type": "text", "text": (
            "Title: Funny Saying Embroidered Trucker Hat\n"
            "URL: https://www.amazon.com/Funny-Trucker-Hat/dp/B0FWK3Q83P?tag=tracker\n"
            "Description: Classic embroidered cap")},
            {"type": "text", "text": (
            "Title: Hat Catalog\n"
            "URL: https://www.amazon.com/clp/B0CRSBTQHD\n"
            "Description: Trucker hats")}]
        accepted = auto.parse_marketplace_results(
            "amazon", blocks, "site:amazon.com/dp trucker hat", auto.TAVILY_PROVIDER)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["url"], "https://www.amazon.com/dp/B0FWK3Q83P")

    def test_tavily_http_429_is_not_misread_as_search_success(self):
        client = auto.TavilyKeylessSearch()
        client.http.post = lambda *a, **k: type("Response", (), {"status_code": 429})()
        with self.assertRaisesRegex(RuntimeError, "tavily_rate_limited"):
            client.search("site:pinterest.com/pin embroidered cap")

    def test_brave_search_uses_only_official_api_and_item_urls(self):
        class Response:
            status_code = 200
            headers = {"content-type": "application/json"}
            content = b'{}'
            def json(self):
                return {"web": {"results": [
                    {"title": "MADE TO WORSHIP embroidered trucker hat",
                     "url": "https://www.etsy.com/listing/123456789/cap?ref=test",
                     "description": "A lightweight baseball cap with lettering"},
                    {"title": "Ignore other domains", "url": "https://unsafe.test/private",
                     "description": "cap"},
                ]}}
        client = auto.BraveWebSearch()
        calls = []
        def get(url, **kwargs):
            calls.append((url, kwargs))
            return Response()
        client.http.get = get
        with patch.dict(os.environ, {"CAM_RESEARCH_BRAVE_API_KEY": "test-private-key"}):
            blocks = client.search("site:etsy.com/listing embroidered trucker hat")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], auto.BRAVE_ENDPOINT)
        self.assertEqual(calls[0][1]["headers"]["X-Subscription-Token"], "test-private-key")
        self.assertFalse(calls[0][1]["allow_redirects"])
        self.assertNotIn("test-private-key", str(calls[0][1]["params"]))
        items = auto.parse_marketplace_results("etsy", blocks, "cap", auto.BRAVE_PROVIDER)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://www.etsy.com/listing/123456789")
        self.assertIn("brave-search", items[0]["excerpt"])

    def test_brave_429_safely_preserves_outbox_and_provider_cooldown(self):
        class LimitedBrave:
            provider = auto.BRAVE_PROVIDER
            def search(self, query):
                raise RuntimeError("brave_rate_limited")
        with tempfile.TemporaryDirectory() as folder:
            with auto.init_db(Path(folder) / "state.sqlite") as db:
                with patch.object(auto, "service_gate", return_value=True), patch.object(auto, "heartbeat"):
                    result = auto.run_once(db, search_provider=LimitedBrave())
                    self.assertEqual(result["status"], "error")
                    self.assertEqual(result["provider"], auto.BRAVE_PROVIDER)
                    self.assertTrue(auto._future_cooldown(db, "brave_cooldown_until"))
                    self.assertFalse(auto._future_cooldown(db, "cooldown_until"))
                    later = auto.run_once(db, search_provider=LimitedBrave())
                    self.assertEqual(later["status"], "provider_cooldown")
                    self.assertEqual(result["queries_today"], later["queries_today"])

    def test_auto_switches_from_brave_to_authenticated_exa_on_provider_cooldown(self):
        with tempfile.TemporaryDirectory() as folder:
            with auto.init_db(Path(folder) / "state.sqlite") as db:
                auto.prepare_state(db)
                paired = {"CAM_RESEARCH_BRAVE_API_KEY": "brave-test-key",
                          "CAM_RESEARCH_EXA_API_KEY": "exa-test-key",
                          "CAM_RESEARCH_SEARCH_PROVIDER": "auto"}
                with patch.dict(os.environ, paired):
                    self.assertIsInstance(auto.configured_search(db), auto.TavilyKeylessSearch)
                    auto.save_meta(db, tavily_cooldown_until=(datetime.now(timezone.utc)
                                  + timedelta(hours=6)).isoformat())
                    self.assertIsInstance(auto.configured_search(db), auto.BraveWebSearch)
                    auto.save_meta(db, brave_cooldown_until=(datetime.now(timezone.utc)
                                  + timedelta(hours=6)).isoformat())
                    self.assertIsInstance(auto.configured_search(db), auto.ExaMcpSearch)
                    # Anonymous Exa cooldown must not block a keyed account.
                    auto.save_meta(db, cooldown_until=(datetime.now(timezone.utc)
                                  + timedelta(hours=6)).isoformat())
                    self.assertIsInstance(auto.configured_search(db), auto.ExaMcpSearch)
                    auto.save_meta(db, exa_keyed_cooldown_until=(datetime.now(timezone.utc)
                                  + timedelta(hours=6)).isoformat())
                    self.assertIsInstance(auto.configured_search(db), auto.TavilyKeylessSearch)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM discovery_attempt").fetchone()[0], 0)

    @patch.object(auto, "service_gate", return_value=True)
    @patch.object(auto, "heartbeat")
    def test_successful_brave_discovery_preserves_exa_cooldown(self, heartbeat, gate):
        class SuccessfulBrave(FakeSearch):
            provider = auto.BRAVE_PROVIDER
        with tempfile.TemporaryDirectory() as folder:
            with auto.init_db(Path(folder) / "research.sqlite3") as db:
                auto.prepare_state(db)
                old_exa_cooldown = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
                auto.save_meta(db, cooldown_until=old_exa_cooldown)
                result = auto.run_once(db, search_provider=SuccessfulBrave(), max_queries=2)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["provider"], auto.BRAVE_PROVIDER)
                self.assertEqual(result["pending_outbox"], 2)
                self.assertEqual(auto.meta(db, "cooldown_until"), old_exa_cooldown)
                self.assertEqual(auto.meta(db, "brave_cooldown_until"), "")

    def test_brave_requires_key_and_never_uses_anonymous_endpoint(self):
        client = auto.BraveWebSearch()
        with patch.dict(os.environ, {"CAM_RESEARCH_BRAVE_API_KEY": ""}):
            with self.assertRaisesRegex(RuntimeError, "brave_not_configured"):
                client.search("site:etsy.com/listing embroidered cap")
        self.assertGreaterEqual(len(auto.SEARCH_QUERIES), 18)
        self.assertEqual(set(source for source, _ in auto.SEARCH_QUERIES),
                         {"etsy", "amazon", "pinterest"})

    def test_exa_rpc_json_rate_limit_is_cooldown_not_generic_error(self):
        client = auto.ExaMcpSearch()
        client.http.post = lambda *args, **kwargs: type("Response", (), {
            "status_code": 200, "headers": {"content-type": "application/json"},
            "content": b'{"jsonrpc":"2.0","error":{"code":-32000,"message":"Rate limit exceeded"},"id":2}',
            "json": lambda self: {"error": {"code": -32000, "message": "Rate limit exceeded"}},
        })()
        with self.assertRaisesRegex(RuntimeError, "exa_rate_limited"):
            client.rpc("tools/call", {}, 2)

    def test_exa_mcp_notification_429_enters_cooldown(self):
        client = auto.ExaMcpSearch()
        client.http.post = lambda *args, **kwargs: type("Response", (), {"status_code": 429})()
        with self.assertRaisesRegex(RuntimeError, "exa_rate_limited"):
            client.rpc("notifications/initialized", {}, None)

    @patch.object(auto, "service_gate", return_value=True)
    @patch.object(auto, "flush_outbox", return_value="")
    @patch.object(auto, "heartbeat")
    def test_cooldown_reports_rate_limit_in_heartbeat(self, heartbeat, flush, gate):
        with tempfile.TemporaryDirectory() as folder:
            with auto.init_db(Path(folder) / "state.sqlite") as db:
                auto.prepare_state(db)
                auto.save_meta(db, cooldown_until=(datetime.now(timezone.utc)
                              + timedelta(hours=2)).isoformat(), last_error="exa_rate_limited")
                result = auto.run_once(db, search_provider=FakeSearch())
        self.assertEqual(result["status"], "provider_cooldown")
        heartbeat.assert_called_once_with("error", "exa_rate_limited")

    @patch.object(auto.requests, "Session")
    def test_rejected_heartbeat_never_breaks_discovery(self, session_factory):
        response = type("Response", (), {"status_code": 403})()
        session_factory.return_value.post.return_value = response
        paired = {"CAM_RESEARCH_SCOUT_AGENT_ID": "12345678-1234-1234-1234-123456789abc",
                  "CAM_RESEARCH_SCOUT_TOKEN": "test-only-token-with-minimum-length"}
        with patch.dict(os.environ, paired):
            self.assertIsNone(auto.heartbeat("ready"))

    def test_rate_limit_inside_successful_mcp_response_is_not_treated_as_results(self):
        client = auto.ExaMcpSearch()
        client.initialized = True
        client.rpc = lambda *args, **kwargs: {
            "_meta": {"ai.exa/rateLimited": True},
            "content": [{"type": "text", "text": "You've hit Exa's free limit"}],
        }
        with self.assertRaisesRegex(RuntimeError, "exa_rate_limited"):
            client.search("site:etsy.com/listing trucker hat")

    def test_url_canonicalization_refuses_malformed_or_untrusted_host(self):
        for url in ("https://www.etsy.com.evil.example/listing/123456",
                    "https://127.0.0.1/listing/123456",
                    "http://www.etsy.com/listing/123456",
                    "https://www.etsy.com/search?q=cap"):
            with self.assertRaises(ValueError):
                auto.canonical_marketplace_url("etsy", url)


class ShopAwareDiscoverTests(unittest.TestCase):
    agent = "12345678-1234-1234-1234-123456789abc"
    token = "fixture-only-research-token-long-enough"

    @staticmethod
    def shop(identifier, platform="etsy", status="active"):
        url = ("https://www.etsy.com/shop/CapSayingStudio" if platform == "etsy"
               else "https://www.amazon.com/stores/HatQuoteCo/page/US01")
        return {"id": identifier, "platform": platform, "status": status,
                "storefront_url": url, "display_name": "Cap Saying Studio" if platform == "etsy" else "Hat Quote Co"}

    def test_shop_query_uses_only_bounded_canonical_public_us_identity(self):
        etsy = self.shop(self.agent)
        amazon = self.shop("12345678-1234-1234-1234-123456789abd", "amazon", "draft")
        self.assertEqual(auto.shop_target(etsy)["query"],
                         'site:etsy.com/listing "Cap Saying Studio" embroidered cap')
        self.assertEqual(auto.shop_target(amazon)["query"],
                         'site:amazon.com/dp "Hat Quote Co" embroidered cap')
        self.assertIsNone(auto.shop_target({**etsy, "status": "paused"}))
        self.assertIsNone(auto.shop_target({**etsy, "status": "archived"}))
        self.assertIsNone(auto.shop_target({**etsy, "platform": "shopify"}))
        self.assertIsNone(auto.shop_target({**etsy, "storefront_url": "https://www.etsy.com.evil.test/shop/CapSayingStudio"}))
        self.assertIsNone(auto.shop_target({**etsy, "storefront_url": "http://www.etsy.com/shop/CapSayingStudio"}))
        self.assertIsNone(auto.shop_target({**etsy, "storefront_url": "https://www.etsy.com/shop/CapSayingStudio?token=123"}))
        self.assertIsNone(auto.shop_target({**etsy, "storefront_url": "https://www.etsy.com/shop/CapSayingStudio/x"}))
        self.assertIsNone(auto.shop_target({**amazon, "storefront_url": "https://www.amazon.co.uk/stores/HatQuoteCo"}))
        self.assertIsNone(auto.shop_target({**amazon, "storefront_url": "https://www.amazon.com/stores/HatQuoteCo/../redirect"}))
        self.assertIsNone(auto.shop_target({**etsy, "id": "arbitrary-id"}))
        hostile = self.shop(self.agent)
        hostile["display_name"] = 'A" site:evil.example 123\nOR sensitive:site'
        query = auto.shop_target(hostile)["query"]
        self.assertNotIn("site:evil.example", query)
        self.assertNotIn("\n", query)
        self.assertTrue(query.startswith("site:etsy.com/listing "))
        self.assertLess(len(query), 120)

    def test_authenticated_catalog_has_fixed_origin_no_marketplace_requests(self):
        data = {"scan_enabled": False, "dispatch": {"state": "disabled", "active_scan_jobs": 0},
                "count": 2,
                "items": [self.shop(self.agent),
                          self.shop("12345678-1234-1234-1234-123456789abd", "amazon")]}
        response = Mock(status_code=200, content=b'{"items":[]}',
                        headers={"content-type": "application/json"}, json=lambda: data)
        with patch.dict(os.environ, {"CAM_RESEARCH_SCOUT_AGENT_ID": self.agent,
                                     "CAM_RESEARCH_SCOUT_TOKEN": self.token}), patch.object(
                                         auto.requests, "Session") as sessions:
            sessions.return_value.get.return_value = response
            targets = auto.fetch_cam_shop_targets()
        self.assertEqual([x["source"] for x in targets], ["etsy", "amazon"])
        self.assertFalse(sessions.return_value.trust_env)
        sessions.return_value.get.assert_called_once()
        args, kwargs = sessions.return_value.get.call_args
        self.assertEqual(args[0], auto.SHOP_LIBRARY_ENDPOINT + "/" + self.agent + "/shops")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer " + self.token)
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["timeout"], (5, 12))
        sessions.return_value.close.assert_called_once()

    def test_catalog_contract_token_and_failure_modes_are_fail_closed(self):
        rows = [self.shop(self.agent)]
        base = {"scan_enabled": False, "dispatch": {"state": "disabled", "active_scan_jobs": 0},
                "count": 1, "items": rows}
        with patch.dict(os.environ, {"CAM_RESEARCH_SCOUT_AGENT_ID": self.agent,
                                     "CAM_RESEARCH_SCOUT_TOKEN": self.token}), patch.object(
                                         auto.requests, "Session") as sessions:
            response = Mock(status_code=200, content=b'{}',
                            headers={"content-type": "application/json"})
            sessions.return_value.get.return_value = response
            for value in (
                {**base, "scan_enabled": True},
                {**base, "dispatch": {"state": "enabled", "active_scan_jobs": 0}},
                {**base, "dispatch": {"state": "disabled", "active_scan_jobs": 1}},
                {**base, "count": 3},
                {**base, "items": [rows[0], rows[0]], "count": 2},
            ):
                response.json.return_value = value
                with self.assertRaisesRegex(RuntimeError, "^cam_shop_catalog_unavailable$"):
                    auto.fetch_cam_shop_targets()
            response.content = b'x' * (auto.SHOP_FETCH_MAX_BYTES + 1)
            with self.assertRaisesRegex(RuntimeError, "^cam_shop_catalog_unavailable$"):
                auto.fetch_cam_shop_targets()
            response.content = b'{}'
            response.status_code = 403
            with self.assertRaisesRegex(RuntimeError, "^cam_shop_catalog_unavailable$"):
                auto.fetch_cam_shop_targets()
        with patch.dict(os.environ, {"CAM_RESEARCH_SCOUT_AGENT_ID": self.agent,
                                     "CAM_RESEARCH_SCOUT_TOKEN": ""}), patch.object(
                                         auto.requests, "Session") as sessions:
            with self.assertRaisesRegex(RuntimeError, "^cam_shop_catalog_unavailable$"):
                auto.fetch_cam_shop_targets()
            sessions.assert_not_called()

    def test_durable_oldest_shop_first_and_never_twice_per_day(self):
        a, b, c = [auto.shop_target(self.shop("12345678-1234-1234-1234-123456789ab" + letter))
                   for letter in "cde"]
        with tempfile.TemporaryDirectory() as folder, auto.init_db(
                Path(folder) / "discover.sqlite3") as db:
            auto.prepare_state(db)
            with patch.object(auto, "today", return_value="2026-10-22"):
                order = []
                for _ in range(3):
                    selected = auto.select_shop_target(db, [c, a, b])
                    order.append(selected["id"])
                    auto.record_shop_attempt(db, selected["id"])
                self.assertEqual(order, sorted([a["id"], b["id"], c["id"]]))
                self.assertIsNone(auto.select_shop_target(db, [a, b, c]))
            with patch.object(auto, "today", return_value="2026-10-23"):
                self.assertEqual(auto.select_shop_target(db, [a, b, c])["id"], a["id"])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM discovery_shop_history").fetchone()[0], 3)

    def test_platform_balance_covers_the_one_amazon_shop_early_without_starvation(self):
        targets = [auto.shop_target(self.shop("12345678-1234-1234-1234-" + f"{i:012d}"))
                   for i in range(32)]
        amazon = auto.shop_target(self.shop("12345678-1234-1234-1234-999999999999", "amazon"))
        targets.append(amazon)
        with tempfile.TemporaryDirectory() as folder, auto.init_db(
                Path(folder) / "discover.sqlite3") as db:
            auto.prepare_state(db)
            with patch.object(auto, "today", return_value="2026-10-22"):
                first = auto.select_shop_target(db, targets)
                self.assertEqual(first["source"], "amazon")
                auto.record_shop_attempt(db, first["id"])
                second = auto.select_shop_target(db, targets)
                self.assertEqual(second["source"], "etsy")
                auto.record_shop_attempt(db, second["id"])
                rest = []
                for _ in range(31):
                    nxt = auto.select_shop_target(db, targets)
                    rest.append(nxt)
                    auto.record_shop_attempt(db, nxt["id"])
                self.assertTrue(all(s["source"] == "etsy" for s in rest))
                self.assertEqual(len({s["id"] for s in [first, second, *rest]}), 33)
                self.assertIsNone(auto.select_shop_target(db, targets))

    @patch.object(auto, "service_gate", return_value=True)
    @patch.object(auto, "heartbeat")
    def test_shop_and_generic_share_one_free_provider_budget(self, heartbeat, gate):
        target = auto.shop_target(self.shop(self.agent))
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            os.environ, {"CAM_RESEARCH_SHOP_QUERIES_ENABLED": "1"}
        ), patch.object(auto, "fetch_cam_shop_targets", return_value=[target]) as catalog:
            with auto.init_db(Path(folder) / "discover.sqlite3") as db:
                source = FakeSearch()
                source.provider = auto.TAVILY_PROVIDER
                result = auto.run_once(db, max_queries=2, daily_limit=2, search_provider=source)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["queries_today"], 2)
                self.assertEqual(result["shop_queries_today"], 1)
                self.assertEqual(result["shop_catalog_status"], "ready")
                self.assertEqual(source.calls[0], target["query"])
                self.assertEqual(len(source.calls), 2)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM discovery_attempt").fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM discovery_shop_attempt").fetchone()[0], 1)
                self.assertEqual(auto.run_once(db, max_queries=2, daily_limit=2, search_provider=source)["status"],
                                 "rate_budget_exhausted")
                self.assertEqual(len(source.calls), 2)
        catalog.assert_called_once()

    @patch.object(auto, "service_gate", return_value=True)
    @patch.object(auto, "heartbeat")
    def test_single_slot_alternates_and_shop_catalog_failure_uses_fixed_queries(self, heartbeat, gate):
        targets = [auto.shop_target(self.shop("12345678-1234-1234-1234-123456789ab" + ch))
                   for ch in "cde"]
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            os.environ, {"CAM_RESEARCH_SHOP_QUERIES_ENABLED": "1"}
        ), patch.object(auto, "fetch_cam_shop_targets", return_value=targets):
            with auto.init_db(Path(folder) / "discover.sqlite3") as db:
                source = FakeSearch()
                source.provider = auto.TAVILY_PROVIDER
                for _ in range(3):
                    self.assertEqual(auto.run_once(
                        db, max_queries=1, daily_limit=3, search_provider=source)["status"], "ok")
                self.assertEqual(sum("Cap Saying Studio" in q for q in source.calls), 2)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM discovery_shop_attempt").fetchone()[0], 2)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM discovery_attempt").fetchone()[0], 1)
                self.assertEqual(auto.status_dict(db)["queries_today"], 3)
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            os.environ, {"CAM_RESEARCH_SHOP_QUERIES_ENABLED": "1"}
        ), patch.object(auto, "fetch_cam_shop_targets", side_effect=RuntimeError("secret-in-error")):
            with auto.init_db(Path(folder) / "discover.sqlite3") as db:
                source = FakeSearch()
                source.provider = auto.TAVILY_PROVIDER
                result = auto.run_once(db, max_queries=1, daily_limit=1, search_provider=source)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["shop_catalog_status"], "unavailable")
                self.assertNotIn("secret", str(result))
                self.assertEqual(result["shop_queries_today"], 0)
                self.assertEqual(len(source.calls), 1)

    @patch.object(auto, "service_gate", return_value=True)
    @patch.object(auto, "heartbeat")
    def test_shop_queries_never_use_keyed_or_unknown_search_provider(self, heartbeat, gate):
        targets = [auto.shop_target(self.shop(self.agent))]
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            os.environ, {"CAM_RESEARCH_SHOP_QUERIES_ENABLED": "1"}
        ), patch.object(auto, "fetch_cam_shop_targets", return_value=targets) as catalog:
            with auto.init_db(Path(folder) / "state.sqlite") as db:
                search = FakeSearch()  # no trustworthy free-provider assertion
                result = auto.run_once(db, max_queries=2, daily_limit=2, search_provider=search)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["shop_queries_today"], 0)
                self.assertEqual(result["shop_catalog_status"], "free_provider_only")
                self.assertEqual(len(search.calls), 2)
            catalog.assert_not_called()

    @patch.object(auto, "service_gate", return_value=True)
    @patch.object(auto, "heartbeat")
    def test_shop_query_rate_limit_stops_shared_budget_and_cools_down(self, heartbeat, gate):
        class Limited:
            provider = auto.TAVILY_PROVIDER
            def __init__(self):
                self.calls = 0
            def search(self, query):
                self.calls += 1
                raise RuntimeError("tavily_rate_limited")
        target = auto.shop_target(self.shop(self.agent))
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            os.environ, {"CAM_RESEARCH_SHOP_QUERIES_ENABLED": "1"}
        ), patch.object(auto, "fetch_cam_shop_targets", return_value=[target]) as catalog:
            with auto.init_db(Path(folder) / "state.sqlite") as db:
                provider = Limited()
                initial = auto.run_once(db, max_queries=2, daily_limit=2, search_provider=provider)
                self.assertEqual(initial["status"], "error")
                self.assertEqual(initial["last_error"], "tavily_rate_limited")
                self.assertEqual(initial["queries_today"], 1)
                self.assertEqual(initial["shop_queries_today"], 1)
                next_run = auto.run_once(db, max_queries=2, daily_limit=2, search_provider=provider)
                self.assertEqual(next_run["status"], "provider_cooldown")
                self.assertEqual(provider.calls, 1)
                catalog.assert_called_once()

    @patch.object(auto, "heartbeat")
    def test_paused_cooldown_or_disabled_never_queries_shop_catalog(self, heartbeat):
        target = auto.shop_target(self.shop(self.agent))
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            os.environ, {"CAM_RESEARCH_SHOP_QUERIES_ENABLED": "1"}
        ), patch.object(auto, "fetch_cam_shop_targets", return_value=[target]) as catalog:
            with auto.init_db(Path(folder) / "discover.sqlite3") as db:
                with patch.object(auto, "service_gate", return_value=False):
                    self.assertEqual(auto.run_once(db, search_provider=FakeSearch())["status"], "paused")
                auto.prepare_state(db)
                auto.save_meta(db, cooldown_until=(datetime.now(timezone.utc) +
                               timedelta(hours=2)).isoformat())
                with patch.object(auto, "service_gate", return_value=True):
                    self.assertEqual(auto.run_once(db, search_provider=FakeSearch())["status"], "provider_cooldown")
            catalog.assert_not_called()
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            os.environ, {"CAM_RESEARCH_SHOP_QUERIES_ENABLED": "0"}
        ), patch.object(auto, "fetch_cam_shop_targets") as catalog:
            with auto.init_db(Path(folder) / "discover.sqlite3") as db, patch.object(
                    auto, "service_gate", return_value=True):
                auto.run_once(db, search_provider=FakeSearch(), daily_limit=1)
            catalog.assert_not_called()


if __name__ == "__main__":
    unittest.main()