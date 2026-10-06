import asyncio
import re
from threading import Event

import httpx
import pytest
import scout as scout_module

from scout import (
    AutoScoutClient,
    Candidate,
    SCOUT_PACES,
    ScoutHistory,
    _clear_stale_profile_runtime_files,
    _looks_like_profile_launch_collision,
    _parse_windows_profile_owners,
    access_gate,
    allowed_image,
    allowed_pin,
    choose_pin_detail_candidate,
    configure_scout_debug_log,
    configure_scout_remote_log,
    extract_related_candidates,
    extract_visible,
    extract_visible_pin_candidates,
    merge_pin_link_candidates,
    idle_diagnostic_message,
    keyword_candidate_budgets,
    normalize_candidates,
    paced_scroll,
    pin_history_key,
    pinimg_asset_key,
    pinimg_rendition_score,
    quality_prefilter,
    quality_search_query,
    related_seed_candidates,
    resolve_pin_details,
    scan_auto_run,
    scout_debug_event,
    shutdown_scout_remote_log,
    task_search_queries,
    wait_for_pin_growth,
)


def test_windows_profile_argument_pattern_requires_exact_profile_path(tmp_path):
    review_profile = (tmp_path / "pinterest-profile").resolve()
    keyword_profile = (tmp_path / "pinterest-profile-keyword").resolve()
    pattern = scout_module._windows_profile_argument_pattern(review_profile)

    review_command = (
        f'chrome.exe --user-data-dir="{review_profile}" --no-first-run'
    ).casefold()
    fully_quoted_review_command = (
        f'chrome.exe "--user-data-dir={review_profile}" --no-first-run'
    ).casefold()
    keyword_command = (
        f'chrome.exe --user-data-dir="{keyword_profile}" --no-first-run'
    ).casefold()

    assert re.search(pattern, review_command)
    assert re.search(pattern, fully_quoted_review_command)
    assert not re.search(pattern, keyword_command)


def test_windows_profile_owner_output_parser_filters_invalid_rows():
    chrome_pids, scout_pids = _parse_windows_profile_owners(
        "chrome|41360\n"
        "scout|1224\n"
        "chrome|41360\n"
        "other|900\n"
        "chrome|not-a-pid\n"
    )
    assert chrome_pids == (41360,)
    assert scout_pids == (1224,)


def test_profile_launch_collision_detection_matches_windows_target_closed_log():
    assert _looks_like_profile_launch_collision(
        RuntimeError(
            "BrowserType.launch_persistent_context: Target page, context or browser "
            "has been closed; process did exit: exitCode=21"
        )
    )
    assert _looks_like_profile_launch_collision(
        RuntimeError("profile is already in use by another instance of Chromium")
    )
    assert not _looks_like_profile_launch_collision(RuntimeError("Pinterest HTTP 429"))


def test_browser_runtime_failure_detection_matches_closed_or_crashed_browser():
    assert scout_module._looks_like_browser_runtime_failure(
        RuntimeError("TargetClosedError: Target page, context or browser has been closed")
    )
    assert scout_module._looks_like_browser_runtime_failure(
        RuntimeError("Page crashed while waiting for selector")
    )
    assert not scout_module._looks_like_browser_runtime_failure(
        RuntimeError("Pinterest returned HTTP 429")
    )


def test_browser_session_recycles_when_closed_or_too_old():
    class FakePage:
        def __init__(self, closed: bool):
            self.closed = closed

        def is_closed(self):
            return self.closed

    max_age = scout_module.BROWSER_SESSION_MAX_AGE_SECONDS
    assert scout_module._browser_session_needs_recycle(
        FakePage(True),
        FakePage(False),
        100.0,
        now=101.0,
    )
    assert not scout_module._browser_session_needs_recycle(
        FakePage(False),
        FakePage(False),
        100.0,
        now=100.0 + max_age - 1,
    )
    assert scout_module._browser_session_needs_recycle(
        FakePage(False),
        FakePage(False),
        100.0,
        now=100.0 + max_age,
    )


def test_clear_stale_profile_runtime_files_preserves_unrelated_files(tmp_path):
    stale_names = (
        "SingletonCookie",
        "SingletonLock",
        "SingletonSocket",
        "lockfile",
        "DevToolsActivePort",
    )
    for name in stale_names:
        (tmp_path / name).write_text("stale", encoding="utf-8")
    keep = tmp_path / "Cookies"
    keep.write_text("keep", encoding="utf-8")

    removed = _clear_stale_profile_runtime_files(tmp_path)

    assert set(removed) == set(stale_names)
    assert keep.read_text(encoding="utf-8") == "keep"
    for name in stale_names:
        assert not (tmp_path / name).exists()




def test_recover_windows_profile_closes_legacy_scout_and_late_chrome(
    monkeypatch,
    tmp_path,
):
    owner_snapshots = [
        ((18452,), (21012,)),
        ((21116,), ()),
        ((), ()),
        ((), ()),
    ]
    scout_kills: list[tuple[int, ...]] = []
    chrome_kills: list[tuple[int, ...]] = []

    monkeypatch.setattr(scout_module.sys, "platform", "win32")
    monkeypatch.setattr(
        scout_module,
        "_windows_profile_owners",
        lambda _profile: owner_snapshots.pop(0),
    )
    monkeypatch.setattr(
        scout_module,
        "_terminate_windows_process_trees",
        lambda pids: scout_kills.append(tuple(pids)) or len(pids),
    )
    monkeypatch.setattr(
        scout_module,
        "_terminate_windows_profile_chrome",
        lambda pids: chrome_kills.append(tuple(pids)) or len(pids),
    )
    monkeypatch.setattr(scout_module.time, "sleep", lambda _seconds: None)

    scout_module._recover_windows_scout_profile(tmp_path)

    assert scout_kills == [(21012,)]
    assert chrome_kills == [(18452,), (21116,)]
    assert owner_snapshots == []


def test_profile_lock_blocks_another_live_v18_scout(monkeypatch, tmp_path):
    lock_path = tmp_path / scout_module.SCOUT_INSTANCE_LOCK_FILENAME
    lock_path.write_text(
        '{"pid":21668,"version":"rrugc-scout-v25"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(scout_module.sys, "platform", "win32")
    monkeypatch.setattr(
        scout_module,
        "_windows_profile_owners",
        lambda _profile: ((), (21668,)),
    )

    lock = scout_module.ScoutProfileLock(tmp_path)
    with pytest.raises(RuntimeError, match="already owns this profile lock"):
        lock.acquire()

    assert lock_path.exists()


def test_profile_lock_replaces_stale_owner_and_releases(monkeypatch, tmp_path):
    lock_path = tmp_path / scout_module.SCOUT_INSTANCE_LOCK_FILENAME
    lock_path.write_text(
        '{"pid":21668,"version":"rrugc-scout-v25"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(scout_module.sys, "platform", "win32")
    monkeypatch.setattr(
        scout_module,
        "_windows_profile_owners",
        lambda _profile: ((), ()),
    )

    lock = scout_module.ScoutProfileLock(tmp_path)
    lock.acquire()

    payload = scout_module.json.loads(lock_path.read_text(encoding="utf-8"))
    assert payload["pid"] == scout_module.os.getpid()
    assert payload["version"] == scout_module.CLIENT_VERSION

    lock.release()
    assert not lock_path.exists()


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
    reloaded = ScoutHistory(path)
    assert reloaded.seen_pin_keys("campaign-a") == {
        "https://www.pinterest.com/pin/123/",
        "https://www.pinterest.com/pin/456/",
    }
    assert reloaded.seen_asset_keys("campaign-a") == {"a.jpg", "b.jpg"}


def test_quality_first_query_and_metadata_prefilter():
    assert quality_search_query("cap man") == "cap man authentic smartphone candid photo real people"
    assert quality_search_query("cap man candid photo") == "cap man candid photo"
    assert quality_search_query("embroidered cap product photo") == (
        "embroidered cap product photo real person product review"
    )
    assert quality_search_query("hand holding embroidered hat photo") == (
        "hand holding embroidered hat photo real human hand product review"
    )

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
            "pin_url": "https://www.pinterest.com/pin/video-456/",
            "image_url": "https://i.pinimg.com/video-thumbnail.jpg",
            "is_video": True,
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


def test_extract_visible_uses_normalization_contract_and_card_fallback():
    class FakePage:
        def __init__(self):
            self.script = ""

        async def evaluate(self, script):
            self.script = script
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

    page = FakePage()
    rows = asyncio.run(extract_visible(page))
    assert len(rows) == 1
    assert rows[0].image_url.endswith("/736x/a.jpg")
    assert 'img[src*="pinimg.com"]' in page.script
    assert "depth < 10" in page.script
    assert "nearestImageForAnchor" in page.script
    assert "pairing_strategy" in page.script


def test_merge_pin_link_candidates_keeps_unpaired_pins_for_detail_resolution():
    rich = [
        Candidate(
            "https://www.pinterest.com/pin/111/",
            "https://i.pinimg.com/736x/aa/111.jpg",
        )
    ]
    rows = merge_pin_link_candidates(
        rich,
        [
            "https://www.pinterest.com/pin/111/?utm_source=feed",
            "https://www.pinterest.com/pin/222/",
        ],
    )
    assert [row.pin_url for row in rows] == [
        "https://www.pinterest.com/pin/111/",
        "https://www.pinterest.com/pin/222/",
    ]
    assert rows[1].image_url == ""


def test_pin_detail_selection_populates_pin_only_candidate():
    seed = Candidate("https://www.pinterest.com/pin/222/", "")
    resolved = choose_pin_detail_candidate(
        seed,
        [
            {
                "url": "https://i.pinimg.com/originals/aa/bb/detail.jpg",
                "source": "meta",
                "width": 1200,
                "height": 1600,
            }
        ],
    )
    assert resolved.image_url == "https://i.pinimg.com/originals/aa/bb/detail.jpg"


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


def test_pin_detail_resolver_rethrows_closed_browser_runtime():
    class ClosedDetailPage:
        async def goto(self, *_args, **_kwargs):
            raise RuntimeError(
                "TargetClosedError: Target page, context or browser has been closed"
            )

    seed = Candidate(
        "https://www.pinterest.com/pin/123/",
        "https://i.pinimg.com/736x/aa/123/photo.jpg",
    )
    with pytest.raises(RuntimeError, match="TargetClosedError"):
        asyncio.run(
            resolve_pin_details(
                object(),
                [seed],
                detail_page=ClosedDetailPage(),
            )
        )


def test_review_supervisor_stops_and_reports_after_restart_limit(monkeypatch):
    calls = 0
    reports = []

    async def fake_run_agent(_args):
        nonlocal calls
        calls += 1
        raise scout_module.ScoutRestartRequested(
            "review_scout_runtime_error_threshold",
            healthy_progress=False,
            last_error_type="RuntimeError",
        )

    async def fake_report(**kwargs):
        reports.append(kwargs)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(scout_module, "run_agent", fake_run_agent)
    monkeypatch.setattr(scout_module, "report_scout_fatal_status", fake_report)
    monkeypatch.setattr(scout_module.asyncio, "sleep", no_sleep)

    args = type(
        "Args",
        (),
        {
            "machine_label": "SCOUT-PC",
            "base_url": "https://example.test",
            "agent_id": "agent-1",
            "token": "secret",
        },
    )()

    with pytest.raises(scout_module.ScoutFatalStop):
        asyncio.run(scout_module.supervise_review_scout(args))

    assert calls == scout_module.SCOUT_MAX_AUTOMATIC_RESTARTS + 1
    assert len(reports) == 1
    assert reports[0]["error_code"] == "review_scout_restart_limit_exceeded"
    assert reports[0]["machine_label"] == "SCOUT-PC"


def test_pinterest_login_ready_marker_is_profile_scoped(tmp_path):
    assert not scout_module.pinterest_login_ready(tmp_path)
    scout_module.mark_pinterest_login_ready(tmp_path)
    assert scout_module.pinterest_login_ready(tmp_path)
    marker = scout_module.pinterest_login_marker_path(tmp_path)
    assert marker.parent == tmp_path.resolve()
    scout_module.clear_pinterest_login_ready(tmp_path)
    assert not scout_module.pinterest_login_ready(tmp_path)


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


def test_access_gate_detects_verifying_browser_and_visible_login_cta():
    class VerifyingPage:
        url = "https://www.pinterest.com/search/pins/?q=test"

        def __init__(self):
            self.calls = 0

        async def evaluate(self, _script):
            self.calls += 1
            if self.calls == 1:
                return False
            return {"verifying": True, "loginVisible": False}

    class LoggedOutPage:
        url = "https://www.pinterest.com/"

        def __init__(self):
            self.calls = 0

        async def evaluate(self, _script):
            self.calls += 1
            if self.calls == 1:
                return False
            return {"verifying": False, "loginVisible": True}

    assert asyncio.run(access_gate(VerifyingPage())) == "challenge"
    assert asyncio.run(access_gate(LoggedOutPage())) == "login"


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
                        "pin_url": "https://www.pinterest.com/pin/reposted-old/",
                        "image_url": "https://i.pinimg.com/originals/old.jpg",
                        "alt_text": "same old image reposted under another Pin",
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
    assert "https://www.pinterest.com/pin/new/" in history.seen_pin_keys(
        "campaign-history"
    )
    assert "new.jpg" in history.seen_asset_keys("source-plan-history")
    assert "new.jpg" in history.seen_asset_keys("campaign-history")
    assert client.completed == ["run-history:completed"]


def test_scan_auto_run_scrolls_past_history_only_results(tmp_path):
    class FakeMouse:
        def __init__(self, page):
            self.page = page

        async def wheel(self, _x, _y):
            self.page.scrolled = True

    class FakePage:
        def __init__(self):
            self.url = ""
            self.scrolled = False
            self.mouse = FakeMouse(self)

        async def goto(self, url, **_kwargs):
            self.url = url
            self.scrolled = False

        async def wait_for_timeout(self, _milliseconds):
            return None

        async def evaluate(self, script):
            if "const selectors" in script:
                return False
            if "querySelectorAll('a[href*=\"/pin/\"] img')" in script:
                return 2 if self.scrolled else 1
            if "const out = []" in script:
                rows = [
                    {
                        "pin_url": "https://www.pinterest.com/pin/old/",
                        "image_url": "https://i.pinimg.com/736x/old.jpg",
                        "alt_text": "old result",
                    }
                ]
                if self.scrolled:
                    rows.append({
                        "pin_url": "https://www.pinterest.com/pin/new-after-scroll/",
                        "image_url": "https://i.pinimg.com/736x/new-after-scroll.jpg",
                        "alt_text": "new result after old history",
                    })
                return rows
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
                "pipeline_count": len(self.submitted),
                "target_count": 10,
                "campaign_status": "running",
            }

        async def complete(self, run_id, status, **_kwargs):
            self.completed.append(run_id + ":" + status)
            return {}

    history = ScoutHistory(tmp_path / "history.json")
    history.remember(
        "campaign-history-scroll",
        [Candidate(
            "https://www.pinterest.com/pin/old/",
            "https://i.pinimg.com/originals/old.jpg",
        )],
    )
    page = FakePage()
    client = FakeClient()
    asyncio.run(scan_auto_run(
        page,
        client,
        {
            "run": {"id": "run-history-scroll"},
            "campaign_id": "campaign-history-scroll",
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

    assert page.scrolled is True
    assert client.submitted == [
        "https://www.pinterest.com/pin/new-after-scroll/"
    ]
    assert client.completed == ["run-history-scroll:completed"]


def test_related_seed_history_is_persistent_and_normalized(tmp_path):
    history = ScoutHistory(tmp_path / "history.json")
    seed = "https://pinterest.com/pin/12345/?utm_source=feed"

    assert history.expanded_seed_keys("plan-1") == set()
    assert history.remember_expanded_seed("plan-1", seed) is True
    assert history.remember_expanded_seed("plan-1", seed) is False

    reloaded = ScoutHistory(tmp_path / "history.json")
    assert reloaded.expanded_seed_keys("plan-1") == {
        "https://www.pinterest.com/pin/12345/"
    }


def test_extract_related_candidates_keeps_first_60_and_excludes_seed():
    class FakeMouse:
        async def wheel(self, _x, _y):
            raise AssertionError("no scroll should be needed when 60 related Pins are loaded")

    class FakePage:
        def __init__(self):
            self.url = ""
            self.mouse = FakeMouse()

        async def goto(self, url, **_kwargs):
            self.url = url
            return None

        async def wait_for_timeout(self, _milliseconds):
            return None

        async def evaluate(self, script):
            if "const selectors" in script:
                return False
            if "const out = []" in script:
                rows = [{
                    "pin_url": "https://www.pinterest.com/pin/seed/",
                    "image_url": "https://i.pinimg.com/736x/seed.jpg",
                    "alt_text": "seed",
                }]
                rows.extend({
                    "pin_url": f"https://www.pinterest.com/pin/related-{index}/",
                    "image_url": f"https://i.pinimg.com/736x/related-{index}.jpg",
                    "alt_text": f"related {index}",
                } for index in range(70))
                return rows
            return False

    seed = Candidate(
        "https://www.pinterest.com/pin/seed/",
        "https://i.pinimg.com/736x/seed.jpg",
    )
    rows = asyncio.run(extract_related_candidates(
        FakePage(),
        seed,
        pace=SCOUT_PACES["careful"],
    ))

    assert len(rows) == 60
    assert rows[0].pin_url.endswith("/related-0/")
    assert rows[-1].pin_url.endswith("/related-59/")
    assert all(row.pin_url != seed.pin_url for row in rows)


def test_extract_related_candidates_allows_150_for_keyword_deep_dive():
    class FakeMouse:
        async def wheel(self, _x, _y):
            raise AssertionError("no scroll should be needed when 150 related Pins are loaded")

    class FakePage:
        def __init__(self):
            self.url = ""
            self.mouse = FakeMouse()

        async def goto(self, url, **_kwargs):
            self.url = url
            return None

        async def wait_for_timeout(self, _milliseconds):
            return None

        async def evaluate(self, script):
            if "const selectors" in script:
                return False
            if "const out = []" in script:
                rows = [{
                    "pin_url": "https://www.pinterest.com/pin/seed-150/",
                    "image_url": "https://i.pinimg.com/736x/seed-150.jpg",
                    "alt_text": "seed",
                }]
                rows.extend({
                    "pin_url": f"https://www.pinterest.com/pin/deep-{index}/",
                    "image_url": f"https://i.pinimg.com/736x/deep-{index}.jpg",
                    "alt_text": f"deep {index}",
                } for index in range(160))
                return rows
            return False

    seed = Candidate(
        "https://www.pinterest.com/pin/seed-150/",
        "https://i.pinimg.com/736x/seed-150.jpg",
    )
    rows = asyncio.run(extract_related_candidates(
        FakePage(),
        seed,
        pace=SCOUT_PACES["careful"],
        limit=150,
    ))

    assert len(rows) == 150
    assert rows[-1].pin_url.endswith("/deep-149/")


def test_auto_scout_expands_approved_seed_before_keyword_search(tmp_path, monkeypatch):
    related = [
        Candidate(
            f"https://www.pinterest.com/pin/related-{index}/",
            f"https://i.pinimg.com/736x/related-{index}.jpg",
            f"related {index}",
        )
        for index in range(20)
    ]

    async def fake_extract_related(_page, seed, *, pace, limit):
        assert seed.pin_url == "https://www.pinterest.com/pin/approved-seed/"
        assert limit == 60
        assert pace.name == "careful"
        return related

    async def fake_resolve(_page, rows, **_kwargs):
        return list(rows)

    monkeypatch.setattr(
        scout_module,
        "extract_related_candidates",
        fake_extract_related,
    )
    monkeypatch.setattr(scout_module, "resolve_pin_details", fake_resolve)

    class FakeMouse:
        async def wheel(self, _x, _y):
            return None

    class FakePage:
        def __init__(self):
            self.visited = []
            self.mouse = FakeMouse()

        async def goto(self, url, **_kwargs):
            self.visited.append(url)
            return None

        async def wait_for_timeout(self, _milliseconds):
            return None

    class FakeClient:
        def __init__(self):
            self.submitted = []
            self.source_queries = []
            self.completed = []

        async def heartbeat(self, *_args, **_kwargs):
            return {}

        async def submit(self, _run_id, rows, *, source_query=None):
            self.submitted.extend(row.pin_url for row in rows)
            self.source_queries.append(source_query)
            count = len(self.submitted)
            return {
                "created": len(rows),
                "existing": 0,
                "progress": 0,
                "pipeline_count": count,
                "target_count": 20,
                "campaign_status": "running",
            }

        async def complete(self, run_id, status, **_kwargs):
            self.completed.append(run_id + ":" + status)
            return {}

    history = ScoutHistory(tmp_path / "history.json")
    page = FakePage()
    client = FakeClient()
    asyncio.run(scan_auto_run(
        page,
        client,
        {
            "run": {"id": "run-related"},
            "campaign_id": "campaign-related",
            "source_plan_id": "plan-related",
            "query": "fallback keyword",
            "search_queries": ["fallback keyword"],
            "related_seeds": [{
                "pin_url": "https://www.pinterest.com/pin/approved-seed/",
                "image_url": "https://i.pinimg.com/736x/approved-seed.jpg",
                "alt_text": "approved",
            }],
            "target_count": 20,
            "max_scroll_batches": 1,
            "progress": 0,
            "pipeline_count": 0,
        },
        login_wait_seconds=60,
        history=history,
    ))

    assert client.submitted == [row.pin_url for row in related]
    assert all(source_query is None for source_query in client.source_queries)
    assert client.completed == ["run-related:completed"]
    assert page.visited == []
    assert history.expanded_seed_keys("campaign-related") == {
        "https://www.pinterest.com/pin/approved-seed/"
    }


def test_related_seed_candidates_reject_invalid_and_dedupe():
    task = {
        "related_seeds": [
            {
                "pin_url": "https://www.pinterest.com/pin/1/",
                "image_url": "https://i.pinimg.com/736x/one.jpg",
                "alt_text": "one",
            },
            {
                "pin_url": "https://www.pinterest.com/pin/1/?duplicate=1",
                "image_url": "https://i.pinimg.com/originals/one.jpg",
            },
            {
                "pin_url": "https://example.com/not-pinterest",
                "image_url": "https://i.pinimg.com/736x/bad.jpg",
            },
        ]
    }
    rows = related_seed_candidates(task)
    assert len(rows) == 1
    assert rows[0].pin_url == "https://www.pinterest.com/pin/1/"


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
                "selfie_wearing_hat": [
                    "grandpa wearing cap selfie phone photo",
                    "outdoor cap selfie natural light",
                ],
                "hand_holding_hat": [
                    "hand holding embroidered cap front view phone photo",
                ],
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
        "grandpa wearing cap selfie phone photo",
        "hand holding embroidered cap front view phone photo",
        "grandpa golf course candid phone photo",
        "family golf outing candid phone photo",
        "Best Grandpa By Par photo",
        "outdoor cap selfie natural light",
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
    assert all(550 <= value <= 950 for value in page.waits)
    assert total == sum(page.mouse.wheels)
    assert pace.submit_batch_size == 3
    assert pace.initial_dwell_ms == (3800, 5800)
    assert pace.inspect_dwell_ms == (1200, 2200)
    assert pace.submit_pause_ms == (1250, 2300)
    assert pace.keyword_pause_ms == (3000, 5000)


def test_scout_debug_log_writes_jsonl_and_keeps_secrets_out_of_events(tmp_path):
    log_path = configure_scout_debug_log(tmp_path)
    scout_debug_event(
        "test_event",
        operation="claim",
        status_code=200,
        duration_ms=42,
    )
    for handler in scout_module._SCOUT_DEBUG_LOGGER.handlers:
        handler.flush()

    rows = [
        scout_module.json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows[-1]["event"] == "test_event"
    assert rows[-1]["operation"] == "claim"
    assert rows[-1]["duration_ms"] == 42
    assert "secret-token" not in log_path.read_text(encoding="utf-8")


def test_scout_remote_log_posts_events_without_blocking_local_logger(
    tmp_path,
    monkeypatch,
):
    posted: list[dict] = []
    sent = Event()

    class FakeResponse:
        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def post(self, url, *, headers, json):
            posted.append({"url": url, "headers": headers, "json": json})
            sent.set()
            return FakeResponse()

    monkeypatch.setattr(scout_module.httpx, "Client", FakeClient)
    configure_scout_debug_log(
        tmp_path,
        filename="keyword-scout.jsonl",
        scout_type="keyword",
    )
    configure_scout_remote_log(
        base_url="https://creative-assets.example",
        agent_id="agent-123",
        token="secret-token",
    )
    try:
        scout_debug_event(
            "keyword_scout_batch_completed",
            visible=31,
            fresh=12,
        )
        assert sent.wait(1.0)
    finally:
        shutdown_scout_remote_log(timeout_seconds=1.0)

    request = posted[-1]
    assert request["url"].endswith(
        "/api/v1/realistic-review-ugc/scout-agents/agent-123/logs"
    )
    assert request["headers"]["Authorization"] == "Bearer secret-token"
    event = request["json"]["events"][0]
    assert event["event_type"] == "keyword_scout_batch_completed"
    assert event["payload"]["scout_type"] == "keyword"
    assert event["payload"]["visible"] == 31
    assert event["payload"]["fresh"] == 12
    assert "secret-token" not in str(event)


def test_scout_debug_log_supports_keyword_specific_jsonl(tmp_path):
    log_path = configure_scout_debug_log(
        tmp_path,
        filename="keyword-scout.jsonl",
        scout_type="keyword",
    )
    scout_debug_event(
        "keyword_scout_test_event",
        query="Saying Trucker hat",
    )
    for handler in scout_module._SCOUT_DEBUG_LOGGER.handlers:
        handler.flush()

    assert log_path.name == "keyword-scout.jsonl"
    rows = [
        scout_module.json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows[0]["event"] == "logging_started"
    assert rows[0]["scout_type"] == "keyword"
    assert rows[-1]["event"] == "keyword_scout_test_event"
    assert rows[-1]["scout_type"] == "keyword"
    assert rows[-1]["query"] == "Saying Trucker hat"


def test_scout_debug_log_rejects_unsafe_filename(tmp_path):
    with pytest.raises(ValueError, match="jsonl basename"):
        configure_scout_debug_log(
            tmp_path,
            filename="../outside.log",
            scout_type="keyword",
        )


def test_idle_diagnostic_message_explains_source_plan_wait():
    message = idle_diagnostic_message({
        "campaigns": [{
            "name": "Hat source",
            "reason": "source_plan_not_ready",
            "target_count": 50,
            "progress": 0,
            "pipeline_count": 0,
            "counts": {},
            "source_plan_statuses": {"queued": 1},
        }],
    })
    assert "waiting for AI Context analysis" in message
    assert "claimable" not in message


def test_idle_diagnostic_message_shows_next_scheduled_scan():
    message = idle_diagnostic_message({
        "campaigns": [{
            "name": "Grandpa cap",
            "reason": "scheduled_later",
            "target_count": 50,
            "progress": 4,
            "pipeline_count": 12,
            "counts": {"analysis_queued": 8, "drive_ready": 4},
            "scan_next_at": "2026-10-05T05:30:49+00:00",
        }],
    })
    assert "next scan is scheduled later" in message
    assert "next_scan=2026-10-05T05:30:49+00:00" in message


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


def test_auto_scout_client_blocks_empty_candidate_submit_before_http():
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(200, json={"status": "ok"})

    async def scenario():
        client = AutoScoutClient(
            "https://cam.example",
            "agent-1",
            "secret-token",
            machine_label="empty-submit-test",
        )
        await client.client.aclose()
        client.client = httpx.AsyncClient(
            base_url="https://cam.example",
            headers={"Authorization": "Bearer secret-token"},
            transport=httpx.MockTransport(handler),
        )
        try:
            with pytest.raises(ValueError, match="at least one resolved image"):
                await client.submit("run-empty", [])
        finally:
            await client.close()

    asyncio.run(scenario())
    assert requests == []


def test_auto_scout_client_uses_agent_scoped_endpoints():
    requests: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.url.path.endswith("/claim"):
            assert request.headers["x-scout-version"] == scout_module.CLIENT_VERSION
            assert request.headers["x-scout-machine"] == "studio-pc"
            return httpx.Response(200, content=b"null", headers={"content-type": "application/json"})
        if request.url.path.endswith("/diagnostics"):
            assert request.headers["x-scout-version"] == scout_module.CLIENT_VERSION
            assert request.headers["x-scout-machine"] == "studio-pc"
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
