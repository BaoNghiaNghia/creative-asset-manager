"""Bounded, offline tests for the Windows Typography Design Scout."""
import asyncio

import httpx

from typography_design_scout import download_image, pin_id, search_queries


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
