import importlib.util
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


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
                    self.assertIsInstance(auto.configured_search(db), auto.BraveWebSearch)
                    auto.save_meta(db, brave_cooldown_until=(datetime.now(timezone.utc)
                                  + timedelta(hours=6)).isoformat())
                    self.assertIsInstance(auto.configured_search(db), auto.ExaMcpSearch)
                    # Previously exhausted anonymous Exa must not block a new
                    # authenticated Exa account.
                    auto.save_meta(db, cooldown_until=(datetime.now(timezone.utc)
                                  + timedelta(hours=6)).isoformat())
                    self.assertIsInstance(auto.configured_search(db), auto.ExaMcpSearch)
                    auto.save_meta(db, exa_keyed_cooldown_until=(datetime.now(timezone.utc)
                                  + timedelta(hours=6)).isoformat())
                    self.assertIsInstance(auto.configured_search(db), auto.BraveWebSearch)
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


if __name__ == "__main__":
    unittest.main()
