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
