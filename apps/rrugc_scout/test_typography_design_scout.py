"""Bounded, offline tests for the Windows Typography Design Scout."""
import asyncio

import httpx

from typography_design_scout import download_image, pin_id, search_queries
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from typography_design_scout import scout


class PickedSourceBootstrapTests(unittest.TestCase):
    def test_live_browser_failure_is_a_reported_partial_success(self):
        with tempfile.TemporaryDirectory() as temp:
            _verify_chrome_launch_failure(Path(temp))

    def test_seed_only_reuses_agent_scoped_pinterest_source_without_chrome(self):
        events = []
        source_pin = "https://www.pinterest.com/pin/123456789012345/"
        previously_imported_pin = "https://www.pinterest.com/pin/987654321012345/"
        source_img = "https://i.pinimg.com/1200x/a/b/123.jpg"

        def respond(request):
            events.append((request.method, request.url.host, request.url.path))
            if request.method == "GET" and request.url.path.endswith("/keywords"):
                return httpx.Response(200, json={
                    "keywords": [{"id": "already-imported", "keyword": "OLD",
                                  "source_pin_url": previously_imported_pin,
                                  "source_image_url": "https://i.pinimg.com/1200x/old.jpg"}],
                    "pending_sources": [{"id": "stage0-1", "keyword": "ADVENTURE AWAITS",
                                         "source_pin_url": source_pin,
                                         "source_image_url": source_img}],
                })
            if request.url.host == "i.pinimg.com":
                return httpx.Response(200, headers={"content-type": "image/png"},
                                      content=b"png-source-bytes")
            if request.method == "POST" and request.url.path.endswith("/references"):
                form = request.content.decode("latin-1")
                self.assertIn("Picked Stage 0 Pinterest reference", form)
                self.assertIn(source_pin, form)
                self.assertNotIn(previously_imported_pin, form)
                return httpx.Response(200, json={"status": "created"})
            raise AssertionError("Unexpected network call: " + str(request.url))

        original_client = httpx.AsyncClient

        def fake_client(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(respond)
            return original_client(*args, **kwargs)

        options = SimpleNamespace(
            base_url="https://cam.example", agent_id="agent-a", keyword_limit=3,
            execute=True, seed_only=True, max_images=8, profile_dir="unused-profile",
            headless=False, chrome_executable="",
        )
        with patch("typography_design_scout.httpx.AsyncClient", side_effect=fake_client):
            with patch.dict(os.environ, {"RRUGC_SCOUT_TOKEN": "test-token"}, clear=False):
                result = asyncio.run(scout(options))
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["picked_source_candidates"], 1)
        self.assertEqual(result["seed_keywords"], 1)
        self.assertEqual(result["new_search_candidates"], 0)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(sum(1 for item in events if item[0] == "POST"), 1)
        self.assertEqual(sum(1 for item in events if item[1] == "i.pinimg.com"), 1)



def _verify_chrome_launch_failure(tmp_path):
    import contextlib
    import io

    original_client = httpx.AsyncClient

    def response(request: httpx.Request):
        if request.method == "GET" and request.url.path.endswith("/keywords"):
            return httpx.Response(200, json={
                "keywords": [{"id": "k1", "keyword": "ADVENTURE AWAITS"}],
                "pending_sources": [{
                    "id": "k1", "keyword": "ADVENTURE AWAITS",
                    "source_pin_url": "https://www.pinterest.com/pin/123456789012345/",
                    "source_image_url": "https://i.pinimg.com/736x/a/b/demo.jpg",
                }],
            })
        if request.url.host == "i.pinimg.com":
            return httpx.Response(200, headers={"content-type": "image/png"}, content=b"real-png-fixture")
        if request.method == "POST" and request.url.path.endswith("/references"):
            return httpx.Response(200, json={"status": "created"})
        raise AssertionError("Unexpected request: " + request.url.path)

    def mock_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(response)
        return original_client(*args, **kwargs)

    options = SimpleNamespace(
        base_url="https://cam.example", agent_id="agent-a", keyword_limit=3,
        execute=True, seed_only=False, max_images=8, profile_dir=tmp_path / "typography",
        headless=False, chrome_executable="",
    )
    stdout = io.StringIO()
    with patch("typography_design_scout.httpx.AsyncClient", side_effect=mock_client):
        with patch("typography_design_scout.launch_context", side_effect=RuntimeError("browser failed")):
            with patch.dict(os.environ, {"RRUGC_SCOUT_TOKEN": "test-token"}, clear=False):
                with contextlib.redirect_stdout(stdout):
                    result = asyncio.run(scout(options))
    assert result["created"] == 1
    assert result["picked_source_candidates"] == 1
    assert result["browser_status"] == "unavailable"
    assert "LIVE_PINTEREST_BROWSER_UNAVAILABLE" in stdout.getvalue()
    assert not (tmp_path / "typography" / ".rrugc-auto-scout-instance.lock").exists()


def test_pin_id_requires_pinterest_https_and_numeric_pin():
    assert pin_id("https://www.pinterest.com/pin/123456789012345/") == "123456789012345"
    assert pin_id("https://pinterest.com/pin/12345") == "12345"
    for bad in (
        "http://www.pinterest.com/pin/123456789/",
        "https://evil.example/pin/123456789/",
        "https://www.pinterest.com/search/pins/?q=hat",
        "https://www.pinterest.com.evil.example/pin/123456789/",
    ):
        assert pin_id(bad) is None


def test_keyword_stays_present_and_queries_are_bounded():
    queries = search_queries("  ADVENTURE    AWAITS  ")
    assert len(queries) == 2
    assert all("ADVENTURE AWAITS" in query for query in queries)
    assert search_queries("  ") == ()
    assert len(search_queries("X" * 400)[0]) < 130


def test_download_rejects_non_pinterest_and_size_without_transferring_too_much():
    def handler(request: httpx.Request):
        return httpx.Response(200, headers={"content-type": "image/jpeg",
                                            "content-length": "9000000"}, content=b"ignored")

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await download_image(client, "https://evil.example/any.jpg") is None
            assert await download_image(client, "https://i.pinimg.com/736x/a/b/c.jpg") is None
    asyncio.run(check())
