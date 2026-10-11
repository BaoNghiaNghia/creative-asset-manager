import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

MODULE = Path(__file__).with_name("research_intelligence.py")
spec = importlib.util.spec_from_file_location("research_intelligence", MODULE)
research = importlib.util.module_from_spec(spec)
spec.loader.exec_module(research)


class ResearchInboxTests(unittest.TestCase):
    def test_deny_internal_and_unapproved_feeds(self):
        for value in [
            "http://news.google.com/rss", "https://localhost/a",
            "https://127.0.0.1/admin", "https://[::1]/",
            "https://evil.com@news.google.com/a",
            "https://news.google.com:8080/rss",
        ]:
            with self.subTest(url=value), self.assertRaises(ValueError):
                research.safe_public_url(value, hosts=research.ALLOWED_FEED_HOSTS)
        with self.assertRaises(ValueError):
            research.safe_public_url("https://untrusted.example/rss", hosts=research.ALLOWED_FEED_HOSTS)

    def test_ingest_dedup_and_manual_approval_before_export(self):
        with tempfile.TemporaryDirectory() as directory:
            with research.init_db(Path(directory) / "inbox.sqlite3") as connection:
                item = {"source": "rss", "channel": "market", "title": "<b>Baseball caps</b>",
                        "url": "https://news.google.com/a", "excerpt": "Trucker hat"}
                added, duplicates = research.ingest(connection, [item, item])
                self.assertEqual((added, duplicates), (1, 1))
                self.assertEqual(research.approved_stage0_batch(connection)["keywords"], [])
                rows = research.list_inbox(connection)
                self.assertEqual(rows[0]["title"], "Baseball caps")
                self.assertEqual(rows[0]["status"], "pending")
                self.assertEqual(research.review(connection, signal_id=rows[0]["id"],
                                                 decision="approved", keyword="TEXAS HATS"), 1)
                self.assertEqual(research.approved_stage0_batch(connection)["keywords"], ["TEXAS HATS"])
                self.assertEqual(research.review(connection, signal_id=rows[0]["id"],
                                                 decision="rejected"), 0)

    def test_marketplace_ingest_normalizes_listing_identity_and_rejects_spoofing(self):
        samples = [
            ("etsy", "https://www.etsy.com/listing/987654321/design?utm_source=x",
             "https://etsy.com/listing/987654321/other",
             "https://www.etsy.com/listing/987654321"),
            ("amazon", "https://www.amazon.com/gp/product/b012345678?tag=tracker",
             "https://amazon.com/dp/B012345678",
             "https://www.amazon.com/dp/B012345678"),
            ("pinterest", "https://pinterest.com/pin/123456789/?ref=tracking",
             "https://www.pinterest.com/pin/123456789",
             "https://www.pinterest.com/pin/123456789"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            with research.init_db(Path(directory) / "inbox.sqlite3") as connection:
                for source, first, second, expected in samples:
                    signals = [{"source": source, "channel": "manual", "title": "Cap artwork",
                                "url": url} for url in (first, second)]
                    self.assertEqual(research.ingest(connection, signals), (1, 1))
                    row = connection.execute(
                        "SELECT url FROM research_signal WHERE source=?", (source,)).fetchone()
                    self.assertEqual(row[0], expected)
                for source, url in (
                    ("amazon", "https://www.amazon.com.evil.example/dp/B012345678"),
                    ("etsy", "https://www.etsy.com/search?q=hat"),
                    ("pinterest", "https://www.etsy.com/listing/123456789"),
                ):
                    self.assertEqual(research.ingest(connection, [{
                        "source": source, "channel": "manual", "title": "Bad",
                        "url": url,
                    }]), (0, 0))
                self.assertEqual(connection.execute(
                    "SELECT COUNT(*) FROM research_signal").fetchone()[0], 3)


    def test_reject_bogus_source_and_missing_title(self):
        with tempfile.TemporaryDirectory() as directory:
            with research.init_db(Path(directory) / "inbox.sqlite3") as connection:
                added, duplicated = research.ingest(connection, [
                    {"source":"unknown","title":"X","url":"https://news.google.com/a"},
                    {"source":"rss","title":"","url":"https://news.google.com/a"},
                    {"source":"rss","title":"X","url":"http://127.0.0.1/a"},
                ])
                self.assertEqual((added,duplicated),(0,0))

    def test_amazon_slug_product_links_normalize_without_crawling(self):
        self.assertEqual(
            research.canonical_marketplace_url(
                "amazon", "https://www.amazon.com/Funny-Trucker-Hat/dp/B0FWK3Q83P?tag=aff"),
            "https://www.amazon.com/dp/B0FWK3Q83P",
        )
        for url in (
            "https://www.amazon.com/clp/B0FWK3Q83P",
            "https://www.amazon.com/embroidered-hat/s",
            "https://www.amazon.com/a/b/dp/B0FWK3Q83P",
            "https://www.amazon.com.evil.example/hat/dp/B0FWK3Q83P",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                research.canonical_marketplace_url("amazon", url)

    def test_keyword_validation(self):
        self.assertEqual(research.validate_keyword("  MADE TO WORSHIP  "), "MADE TO WORSHIP")
        for invalid in ["hello", "<script>", "one", "abc def /;", ""]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                research.validate_keyword(invalid)

    def test_rss_parser_filters_bad_links_and_strips_markup(self):
        try:
            import feedparser
        except ImportError:
            self.skipTest("feedparser is installed only in the isolated venv")
        xml = b'''<?xml version="1.0"?>
        <rss version="2.0"><channel><title>Hats</title>
          <item><title>Trucker hats</title><link>https://news.google.com/one</link>
            <description><![CDATA[<b>Clean</b> text]]></description></item>
          <item><title>Bad URL</title><link>http://127.0.0.1/private</link></item>
        </channel></rss>'''
        results = research.parse_rss(xml,feed_name="test")
        self.assertEqual(len(results),1)
        self.assertEqual(results[0]["excerpt"],"Clean text")

    def test_sync_fails_without_explicit_pairing_and_never_marks_sent(self):
        with tempfile.TemporaryDirectory() as directory:
            with research.init_db(Path(directory) / "inbox.sqlite3") as connection:
                research.ingest(connection, [{
                    "source": "rss", "channel": "hat", "title": "Hat quote",
                    "url": "https://news.google.com/abc",
                }])
                with self.assertRaisesRegex(ValueError, "valid, explicitly paired"):
                    research.sync_to_cam(connection, agent_id="", token="")
                self.assertEqual(len(research.list_inbox(connection)), 1)

    def test_scoped_sync_receipt_marks_delivered_only_after_api_success(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            with research.init_db(Path(directory) / "inbox.sqlite3") as connection:
                research.ingest(connection, [{
                    "source": "rss", "channel": "hats", "title": "Cap news",
                    "url": "https://news.google.com/rss/a",
                }])
                agent_id = "72680eb2-3205-4b27-8a1f-66140db12f5b"
                with patch("requests.Session") as session:
                    mocked = session.return_value.__enter__.return_value
                    mocked.post.return_value.status_code = 500
                    with self.assertRaisesRegex(ValueError, "HTTP 500"):
                        research.sync_to_cam(connection, agent_id=agent_id, token="test-secret-" + "x" * 24)
                    self.assertEqual(len(research.list_inbox(connection)), 1)
                    mocked.post.return_value.status_code = 200
                    mocked.post.return_value.json.return_value = {"added": 1, "duplicates": 0}
                    result = research.sync_to_cam(connection, agent_id=agent_id, token="test-secret-" + "x" * 24)
                    self.assertEqual(result["sent"], 1)
                    self.assertEqual(len(research.list_inbox(connection)), 0)
                    self.assertEqual(len(research.list_inbox(connection, status="synced")), 1)
                    self.assertIn("/scout-agents/" + agent_id + "/signals/import",
                                  mocked.post.call_args.args[0])

    def test_pinned_public_dns_blocks_rebinding_before_request(self):
        from unittest.mock import patch
        with patch.object(research.socket, "getaddrinfo", return_value=[
            (2, 1, 6, "", ("8.8.8.8", 443)),
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ]), patch("urllib3.HTTPSConnectionPool") as pool:
            with self.assertRaisesRegex(ValueError, "not public"):
                research.fetch_rss("https://news.google.com/rss/search?q=cap")
            pool.assert_not_called()

    def test_rss_https_uses_pinned_ip_sni_and_bounded_bytes(self):
        from unittest.mock import patch
        with patch.object(research.socket, "getaddrinfo", return_value=[
            (2, 1, 6, "", ("8.8.8.8", 443)),
        ]), patch("urllib3.HTTPSConnectionPool") as pool_class:
            pool = pool_class.return_value
            response = pool.urlopen.return_value
            response.status = 200
            response.stream.return_value = iter([b"ab", b"cd"])
            self.assertEqual(research.fetch_rss(
                "https://news.google.com/rss/search?q=cap"), b"abcd")
            self.assertEqual(pool_class.call_args.args[:1], ("8.8.8.8",))
            self.assertEqual(pool_class.call_args.kwargs["server_hostname"], "news.google.com")
            self.assertEqual(pool_class.call_args.kwargs["assert_hostname"], "news.google.com")
            self.assertEqual(pool.urlopen.call_args.args,
                             ("GET", "/rss/search?q=cap"))
            self.assertEqual(pool.urlopen.call_args.kwargs["headers"]["Host"],
                             "news.google.com")
            self.assertFalse(pool.urlopen.call_args.kwargs["redirect"])
            response.release_conn.assert_called_once()
            pool.close.assert_called_once()
            response.stream.return_value = iter([b"abcdef"])
            with self.assertRaisesRegex(ValueError, "maximum size"):
                research.fetch_rss("https://news.google.com/rss", max_bytes=3)
            response.release_conn.assert_called()
            pool.close.assert_called()
            response.status = 302
            with self.assertRaisesRegex(ValueError, "non-200"):
                research.fetch_rss("https://news.google.com/rss")

    def test_collection_processes_all_batches_and_reports_partial_sources(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            location = Path(directory)
            config = location / "feeds.json"
            config.write_text(json.dumps({"feeds": [
                {"name": "first", "url": "https://news.google.com/rss/a"},
                {"name": "second", "url": "https://news.google.com/rss/b"},
            ]}), encoding="utf-8")
            signals = [{"source": "rss", "channel": "hats",
                        "title": "Hat story", "excerpt": "",
                        "url": f"https://news.google.com/rss/articles/{index}"}
                       for index in range(55)]
            with patch.object(research, "FEEDS_FILE", config), \
                 patch.object(research, "fetch_rss", side_effect=[b"ok", ValueError("feed failed")]), \
                 patch.object(research, "parse_rss", return_value=signals):
                result = research.collect_once(location / "inbox.sqlite3")
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["sources_ok"], 1)
            self.assertEqual(result["sources_failed"], 1)
            self.assertEqual(result["received"], 55)
            self.assertEqual(result["added"], 55)
            with research.init_db(location / "inbox.sqlite3") as db:
                count = db.execute("SELECT COUNT(*) FROM research_signal").fetchone()[0]
            self.assertEqual(count, 55)

    def test_collection_fails_closed_if_every_source_fails(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            location = Path(directory)
            config = location / "feeds.json"
            config.write_text(json.dumps({"feeds": [{
                "name": "one", "url": "https://news.google.com/rss/a",
            }]}), encoding="utf-8")
            with patch.object(research, "FEEDS_FILE", config), \
                 patch.object(research, "fetch_rss", side_effect=ValueError("feed failed")):
                with self.assertRaisesRegex(ValueError, "No permitted research"):
                    research.collect_once(location / "inbox.sqlite3")
            self.assertFalse((location / "inbox.sqlite3").exists())

    def test_partial_or_invalid_sync_receipt_keeps_pending(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            with research.init_db(Path(directory) / "inbox.sqlite3") as connection:
                research.ingest(connection, [{
                    "source": "rss", "channel": "hats", "title": "Cap news",
                    "url": "https://news.google.com/rss/a",
                }])
                with patch("requests.Session") as session:
                    mocked = session.return_value.__enter__.return_value
                    mocked.post.return_value.status_code = 200
                    for receipt in (
                        {"added": 0, "duplicates": 0},
                        {"added": 2, "duplicates": 0},
                        {"added": True, "duplicates": 0},
                        {"added": -1, "duplicates": 2},
                        {"added": "1", "duplicates": 0},
                    ):
                        mocked.post.return_value.json.return_value = receipt
                        with self.subTest(receipt=receipt), self.assertRaisesRegex(
                                ValueError, "complete batch"):
                            research.sync_to_cam(
                                connection,
                                agent_id="72680eb2-3205-4b27-8a1f-66140db12f5b",
                                token="test-" + "x" * 30)
                        self.assertEqual(len(research.list_inbox(connection)), 1)


if __name__ == "__main__":
    unittest.main()
