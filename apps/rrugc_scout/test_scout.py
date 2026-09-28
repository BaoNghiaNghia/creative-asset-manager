import asyncio

import httpx

from scout import (
    AutoScoutClient,
    SCOUT_PACES,
    access_gate,
    allowed_image,
    allowed_pin,
    extract_visible,
    idle_diagnostic_message,
    normalize_candidates,
    paced_scroll,
    quality_prefilter,
    quality_search_query,
    scan_auto_run,
    wait_for_pin_growth,
)


def test_url_allowlists():
    assert allowed_pin("https://www.pinterest.com/pin/123/")
    assert allowed_pin("https://pinterest.com/pin/abc/")
    assert not allowed_pin("https://www.pinterest.com/search/pins/?q=abc")
    assert not allowed_pin("https://evil.example/pin/123/")
    assert allowed_image("https://i.pinimg.com/736x/a/b/c.jpg")
    assert not allowed_image("https://example.com/image.jpg")


def test_quality_first_query_and_metadata_prefilter():
    assert quality_search_query("cap man") == "cap man authentic candid lifestyle photo real people"
    assert quality_search_query("cap man candid photo") == "cap man candid photo"

    rows = normalize_candidates([
        {
            "pin_url": "https://www.pinterest.com/pin/ai/",
            "image_url": "https://i.pinimg.com/ai.jpg",
            "alt_text": "AI generated fashion portrait made with Midjourney",
        },
        {
            "pin_url": "https://www.pinterest.com/pin/photo/",
            "image_url": "https://i.pinimg.com/photo.jpg",
            "alt_text": "woman laughing outdoors",
            "context_text": "casual summer outfit photography",
        },
    ])
    accepted, filtered = quality_prefilter(rows)
    assert filtered == 1
    assert [row.pin_url for row in accepted] == [
        "https://www.pinterest.com/pin/photo/"
    ]


def test_normalize_candidates_filters_and_dedupes():
    rows = [
        {
            "pin_url": "https://www.pinterest.com/pin/123/",
            "image_url": "https://i.pinimg.com/a.jpg",
            "alt_text": "one",
        },
        {
            "pin_url": "https://www.pinterest.com/pin/123/",
            "image_url": "https://i.pinimg.com/a.jpg",
            "alt_text": "duplicate",
        },
        {
            "pin_url": "https://evil.example/pin/9/",
            "image_url": "https://i.pinimg.com/b.jpg",
        },
    ]
    result = normalize_candidates(rows)
    assert len(result) == 1
    assert result[0].alt_text == "one"


def test_extract_visible_uses_normalization_contract():
    class FakePage:
        async def evaluate(self, _script):
            return [
                {
                    "pin_url": "https://www.pinterest.com/pin/123/",
                    "image_url": "https://i.pinimg.com/736x/a.jpg",
                    "alt_text": "large visible result",
                },
                {
                    "pin_url": "https://evil.example/pin/1/",
                    "image_url": "https://i.pinimg.com/736x/b.jpg",
                },
            ]

    rows = asyncio.run(extract_visible(FakePage()))
    assert len(rows) == 1
    assert rows[0].image_url.endswith("/736x/a.jpg")


def test_access_gate_detects_login_and_challenge_without_solving_them():
    class LoginPage:
        url = "https://www.pinterest.com/login/"

        async def evaluate(self, _script):
            return False

    class CaptchaPage:
        url = "https://www.pinterest.com/search/pins/?q=test"

        async def evaluate(self, _script):
            return True

    assert asyncio.run(access_gate(LoginPage())) == "login"
    assert asyncio.run(access_gate(CaptchaPage())) == "challenge"


def test_auto_scout_scans_every_campaign_keyword():
    class FakeMouse:
        def __init__(self, page):
            self.page = page

        async def wheel(self, _x, _y):
            self.page.pin_count += 1

    class FakePage:
        def __init__(self):
            self.url = ""
            self.visited: list[str] = []
            self.pin_count = 1
            self.mouse = FakeMouse(self)

        async def goto(self, url, **_kwargs):
            self.url = url
            self.visited.append(url)
            self.pin_count = 1

        async def wait_for_timeout(self, _milliseconds):
            return None

        async def evaluate(self, script):
            if "const selectors" in script:
                return False
            if "querySelectorAll('a[href*=\"/pin/\"] img')" in script:
                return self.pin_count
            if "const out = []" in script:
                suffix = "first" if "first+keyword" in self.url else "second"
                return [{
                    "pin_url": f"https://www.pinterest.com/pin/{suffix}/",
                    "image_url": f"https://i.pinimg.com/736x/{suffix}.jpg",
                    "alt_text": suffix,
                }]
            return False

    class FakeClient:
        def __init__(self):
            self.submitted: list[str] = []
            self.completed: list[str] = []

        async def heartbeat(self, *_args, **_kwargs):
            return {}

        async def submit(self, _run_id, rows):
            self.submitted.extend(row.pin_url for row in rows)
            return {
                "created": len(rows),
                "existing": 0,
                "progress": 0,
                "target_count": 100,
                "campaign_status": "running",
            }

        async def complete(self, run_id, status, **_kwargs):
            self.completed.append(run_id + ":" + status)
            return {}

    page = FakePage()
    client = FakeClient()
    asyncio.run(scan_auto_run(
        page,
        client,
        {
            "run": {"id": "run-1"},
            "campaign_id": "campaign-1",
            "query": "first keyword",
            "search_queries": ["first keyword", "second keyword"],
            "target_count": 100,
            "max_scroll_batches": 1,
            "progress": 0,
        },
        login_wait_seconds=60,
    ))

    assert len(page.visited) == 2
    assert "first+keyword" in page.visited[0]
    assert "second+keyword" in page.visited[1]
    assert len(client.submitted) == 2
    assert client.completed == ["run-1:completed"]


def test_careful_pace_uses_gradual_scrolls_and_longer_waits():
    class FakeMouse:
        def __init__(self):
            self.wheels: list[int] = []

        async def wheel(self, _x, y):
            self.wheels.append(y)

    class FakePage:
        def __init__(self):
            self.mouse = FakeMouse()
            self.waits: list[int] = []

        async def wait_for_timeout(self, milliseconds):
            self.waits.append(milliseconds)

    page = FakePage()
    pace = SCOUT_PACES["careful"]
    total = asyncio.run(paced_scroll(page, pace))

    assert 3 <= len(page.mouse.wheels) <= 5
    assert all(420 <= value <= 700 for value in page.mouse.wheels)
    assert all(650 <= value <= 1150 for value in page.waits)
    assert total == sum(page.mouse.wheels)
    assert pace.submit_batch_size == 3
    assert pace.initial_dwell_ms == (4500, 7000)
    assert pace.keyword_pause_ms == (3500, 6000)


def test_idle_diagnostic_message_explains_pipeline_backpressure():
    message = idle_diagnostic_message({
        "campaigns": [{
            "name": "Lifestyle",
            "reason": "pipeline_full",
            "target_count": 100,
            "progress": 3,
            "pipeline_count": 100,
            "counts": {"analysis_queued": 96, "analyzing": 1, "approved": 3},
        }],
    })
    assert "pipeline is full" in message
    assert "progress=3/100" in message
    assert "analysis_queued=96" in message


def test_auto_scout_client_uses_agent_scoped_endpoints():
    requests: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.url.path.endswith("/claim"):
            assert request.headers["x-scout-version"] == "rrugc-scout-v5"
            assert request.headers["x-scout-machine"] == "studio-pc"
            return httpx.Response(200, content=b"null", headers={"content-type": "application/json"})
        return httpx.Response(200, json={"status": "ready"})

    async def scenario():
        client = AutoScoutClient(
            "https://cam.example",
            "agent-1",
            "secret-token",
            machine_label="studio-pc",
        )
        await client.client.aclose()
        client.client = httpx.AsyncClient(
            base_url="https://cam.example",
            headers={"Authorization": "Bearer secret-token"},
            transport=httpx.MockTransport(handler),
        )
        try:
            assert await client.claim() is None
            assert await client.diagnostics() == {"status": "ready"}
            await client.heartbeat("ready")
        finally:
            await client.close()

    asyncio.run(scenario())
    assert requests == [
        ("POST", "/api/v1/realistic-review-ugc/scout-agents/agent-1/claim"),
        ("GET", "/api/v1/realistic-review-ugc/scout-agents/agent-1/diagnostics"),
        ("POST", "/api/v1/realistic-review-ugc/scout-agents/agent-1/heartbeat"),
    ]
