import asyncio

import httpx

from scout import (
    AutoScoutClient,
    Candidate,
    SCOUT_PACES,
    ScoutHistory,
    access_gate,
    allowed_image,
    allowed_pin,
    choose_pin_detail_candidate,
    extract_visible,
    idle_diagnostic_message,
    keyword_candidate_budgets,
    normalize_candidates,
    paced_scroll,
    pin_history_key,
    pinimg_asset_key,
    pinimg_rendition_score,
    quality_prefilter,
    quality_search_query,
    resolve_pin_details,
    scan_auto_run,
    task_search_queries,
    wait_for_pin_growth,
)


def test_url_allowlists():
    assert allowed_pin("https://www.pinterest.com/pin/123/")
    assert allowed_pin("https://pinterest.com/pin/abc/")
    assert not allowed_pin("https://www.pinterest.com/search/pins/?q=abc")
    assert not allowed_pin("https://evil.example/pin/123/")
    assert allowed_image("https://i.pinimg.com/736x/a/b/c.jpg")
    assert not allowed_image("https://example.com/image.jpg")


def test_scout_history_persists_seen_pins(tmp_path):
    path = tmp_path / "history.json"
    history = ScoutHistory(path)
    rows = [
        Candidate(
            "https://pinterest.com/pin/123/?utm_source=test",
            "https://i.pinimg.com/736x/a.jpg",
        ),
        Candidate(
            "https://www.pinterest.com/pin/456/",
            "https://i.pinimg.com/736x/b.jpg",
        ),
    ]
    assert history.remember("campaign-a", rows) == 2
    assert history.remember("campaign-a", rows) == 0
    assert pin_history_key(rows[0].pin_url) == "https://www.pinterest.com/pin/123/"
    assert ScoutHistory(path).seen_pin_keys("campaign-a") == {
        "https://www.pinterest.com/pin/123/",
        "https://www.pinterest.com/pin/456/",
    }


def test_quality_first_query_and_metadata_prefilter():
    assert quality_search_query("cap man") == "cap man authentic smartphone candid photo real people"
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


def test_normalize_candidates_dedupes_pin_and_keeps_best_search_rendition():
    rows = normalize_candidates([
        {
            "pin_url": "https://www.pinterest.com/pin/123/",
            "image_url": "https://i.pinimg.com/236x/aa/bb/photo.jpg",
            "alt_text": "small",
        },
        {
            "pin_url": "https://www.pinterest.com/pin/123/",
            "image_url": "https://i.pinimg.com/736x/aa/bb/photo.jpg",
            "alt_text": "large",
        },
    ])
    assert len(rows) == 1
    assert rows[0].image_url == "https://i.pinimg.com/736x/aa/bb/photo.jpg"
    assert rows[0].alt_text == "large"


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


def test_pin_detail_selection_prefers_same_asset_highest_rendition():
    seed = Candidate(
        "https://www.pinterest.com/pin/123/",
        "https://i.pinimg.com/236x/aa/bb/photo.jpg",
        "search thumbnail",
    )
    assert pinimg_asset_key(seed.image_url) == "aa/bb/photo.jpg"
    assert pinimg_rendition_score(seed.image_url) == 236
    assert pinimg_rendition_score(
        "https://i.pinimg.com/originals/aa/bb/photo.jpg"
    ) > pinimg_rendition_score("https://i.pinimg.com/1200x/aa/bb/photo.jpg")

    resolved = choose_pin_detail_candidate(
        seed,
        [
            {
                "url": "https://i.pinimg.com/originals/xx/yy/related.jpg",
                "source": "image",
                "width": 2000,
                "height": 2000,
            },
            {
                "url": "https://i.pinimg.com/736x/aa/bb/photo.jpg",
                "source": "closeup",
                "width": 736,
                "height": 981,
                "alt_text": "detail image",
            },
            {
                "url": "https://i.pinimg.com/originals/aa/bb/photo.jpg",
                "source": "jsonld",
            },
        ],
    )
    assert resolved.image_url == "https://i.pinimg.com/originals/aa/bb/photo.jpg"
    assert resolved.alt_text == "search thumbnail"


def test_pin_detail_resolver_reuses_one_sequential_detail_tab():
    class FakeContext:
        def __init__(self):
            self.created = 0
            self.closed = 0

        async def new_page(self):
            self.created += 1
            return DetailPage(self)

    class DetailPage:
        def __init__(self, context):
            self.context = context
            self.url = ""
            self.visited: list[str] = []
            self.waits: list[int] = []

        async def goto(self, url, **_kwargs):
            self.url = url
            self.visited.append(url)
            return None

        async def wait_for_timeout(self, milliseconds):
            self.waits.append(milliseconds)

        async def evaluate(self, script):
            if "const selectors" in script:
                return False
            pin_id = self.url.rstrip("/").split("/")[-1]
            return [{
                "url": f"https://i.pinimg.com/originals/aa/{pin_id}/photo.jpg",
                "source": "meta",
                "width": 1200,
                "height": 1600,
            }]

        async def close(self):
            self.context.closed += 1

    class SearchPage:
        def __init__(self):
            self.context = FakeContext()

    page = SearchPage()
    rows = [
        Candidate(
            f"https://www.pinterest.com/pin/{index}/",
            f"https://i.pinimg.com/236x/aa/{index}/photo.jpg",
        )
        for index in range(5)
    ]
    resolved = asyncio.run(resolve_pin_details(page, rows, concurrency=3))
    assert all("/originals/" in row.image_url for row in resolved)
    assert page.context.created == 1
    assert page.context.closed == 1


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


def test_auto_scout_preserves_ranked_keyword_order_and_source_attribution():
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
            self.source_queries: list[str | None] = []
            self.completed: list[str] = []

        async def heartbeat(self, *_args, **_kwargs):
            return {}

        async def submit(self, _run_id, rows, *, source_query=None):
            self.submitted.extend(row.pin_url for row in rows)
            self.source_queries.append(source_query)
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
    assert client.source_queries == ["first keyword", "second keyword"]
    assert client.completed == ["run-1:completed"]


def test_existing_candidates_do_not_exhaust_new_candidate_cap(monkeypatch):
    monkeypatch.setattr("scout.random.shuffle", lambda rows: None)

    class FakeMouse:
        async def wheel(self, _x, _y):
            return None

    class FakePage:
        def __init__(self):
            self.url = ""
            self.visited: list[str] = []
            self.mouse = FakeMouse()

        async def goto(self, url, **_kwargs):
            self.url = url
            self.visited.append(url)

        async def wait_for_timeout(self, _milliseconds):
            return None

        async def evaluate(self, script):
            if "const selectors" in script:
                return False
            if "querySelectorAll('a[href*=\"/pin/\"] img')" in script:
                return 24
            if "const out = []" in script:
                if "first+keyword" in self.url:
                    return [{
                        "pin_url": f"https://www.pinterest.com/pin/existing-{index}/",
                        "image_url": f"https://i.pinimg.com/736x/existing-{index}.jpg",
                        "alt_text": "existing",
                    } for index in range(24)]
                return [{
                    "pin_url": "https://www.pinterest.com/pin/new-second/",
                    "image_url": "https://i.pinimg.com/736x/new-second.jpg",
                    "alt_text": "new",
                }]
            return False

    class FakeClient:
        def __init__(self):
            self.completed: list[str] = []

        async def heartbeat(self, *_args, **_kwargs):
            return {}

        async def submit(self, _run_id, rows, *, source_query=None):
            assert source_query in {"first keyword", "second keyword"}
            existing = all("existing-" in row.pin_url for row in rows)
            return {
                "created": 0 if existing else len(rows),
                "existing": len(rows) if existing else 0,
                "progress": 0,
                "pipeline_count": 0,
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
            "run": {"id": "run-duplicates"},
            "campaign_id": "campaign-duplicates",
            "query": "first keyword",
            "search_queries": ["first keyword", "second keyword"],
            "target_count": 100,
            "max_scroll_batches": 1,
            "progress": 0,
            "pipeline_count": 0,
        },
        login_wait_seconds=60,
    ))

    assert len(page.visited) == 2
    assert "first+keyword" in page.visited[0]
    assert "second+keyword" in page.visited[1]
    assert client.completed == ["run-duplicates:completed"]


def test_scan_auto_run_skips_persisted_pin_history(tmp_path):
    class FakeMouse:
        async def wheel(self, _x, _y):
            return None

    class FakePage:
        def __init__(self):
            self.url = ""
            self.mouse = FakeMouse()

        async def goto(self, url, **_kwargs):
            self.url = url

        async def wait_for_timeout(self, _milliseconds):
            return None

        async def evaluate(self, script):
            if "const selectors" in script:
                return False
            if "querySelectorAll('a[href*=\"/pin/\"] img')" in script:
                return 2
            if "const out = []" in script:
                return [
                    {
                        "pin_url": "https://www.pinterest.com/pin/old/",
                        "image_url": "https://i.pinimg.com/736x/old.jpg",
                        "alt_text": "old result",
                    },
                    {
                        "pin_url": "https://www.pinterest.com/pin/new/",
                        "image_url": "https://i.pinimg.com/736x/new.jpg",
                        "alt_text": "new result",
                    },
                ]
            return False

    class FakeClient:
        def __init__(self):
            self.submitted = []
            self.completed = []

        async def heartbeat(self, *_args, **_kwargs):
            return {}

        async def submit(self, _run_id, rows, *, source_query=None):
            self.submitted.extend(row.pin_url for row in rows)
            return {
                "created": len(rows),
                "existing": 0,
                "progress": 0,
                "pipeline_count": len(rows),
                "target_count": 10,
                "campaign_status": "running",
            }

        async def complete(self, run_id, status, **_kwargs):
            self.completed.append(run_id + ":" + status)
            return {}

    history = ScoutHistory(tmp_path / "history.json")
    history.remember(
        "source-plan-history",
        [Candidate(
            "https://pinterest.com/pin/old/?utm_source=previous",
            "https://i.pinimg.com/236x/old.jpg",
        )],
    )
    client = FakeClient()
    asyncio.run(scan_auto_run(
        FakePage(),
        client,
        {
            "run": {"id": "run-history"},
            "campaign_id": "campaign-history",
            "source_plan_id": "source-plan-history",
            "source_relative_path": "hats/navy/embroidery-front.png",
            "source_name": "embroidery-front.png",
            "query": "phone candid",
            "search_queries": ["phone candid"],
            "target_count": 10,
            "max_scroll_batches": 1,
            "progress": 0,
            "pipeline_count": 0,
        },
        login_wait_seconds=60,
        history=history,
    ))

    assert client.submitted == ["https://www.pinterest.com/pin/new/"]
    assert "https://www.pinterest.com/pin/new/" in history.seen_pin_keys(
        "source-plan-history"
    )
    assert not history.seen_pin_keys("campaign-history")
    assert client.completed == ["run-history:completed"]


def test_keyword_candidate_budgets_cap_keywords_and_share_capacity():
    assert keyword_candidate_budgets(24, 10) == [6, 6, 6, 6]
    assert keyword_candidate_budgets(12, 10) == [3, 3, 3, 3]
    assert keyword_candidate_budgets(3, 10) == [1, 1, 1]
    assert keyword_candidate_budgets(0, 5) == []


def test_source_plan_search_queries_prioritize_image_context_over_legacy_queries():
    task = {
        "source_plan_id": "source-plan-context",
        "query": "generic candid lifestyle",
        "search_queries": [
            "generic candid lifestyle",
            "casual cap phone photo",
        ],
        "source_context": {
            "search_clusters": {
                "direct": [
                    "grandpa golf course candid phone photo",
                    "grandfather tee time candid phone photo",
                ],
                "text_match": [
                    "Best Grandpa By Par photo",
                    "Best Grandpa By Par candid photo",
                ],
                "adjacent": ["family golf outing candid phone photo"],
                "generic": ["casual lifestyle candid phone photo"],
            }
        },
    }

    assert task_search_queries(task)[:6] == [
        "grandpa golf course candid phone photo",
        "grandfather tee time candid phone photo",
        "Best Grandpa By Par photo",
        "Best Grandpa By Par candid photo",
        "family golf outing candid phone photo",
        "generic candid lifestyle",
    ]


def test_legacy_search_queries_keep_server_order_without_source_plan_context():
    task = {
        "query": "first query",
        "search_queries": ["first query", "second query"],
        "source_context": {
            "search_clusters": {"direct": ["should not be promoted"]},
        },
    }
    assert task_search_queries(task) == ["first query", "second query"]


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
            assert request.headers["x-scout-version"] == "rrugc-scout-v14"
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
