"""Regression tests for the shared four-agent bandwidth governor."""
import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import network_load
from network_load import (
    apply_low_bandwidth_routes, scout_network_slot,
    wait_between_products, wait_for_visible_pin_images,
)


def test_all_four_scout_modes_share_one_os_navigation_slot(tmp_path):
    async def exercise():
        path = tmp_path / "same-windows-checkout.lock"
        active = 0
        peak = 0
        acquired = []
        events = []

        async def agent(mode):
            nonlocal active, peak
            async with scout_network_slot(
                mode, path=path, log=lambda *a, **kw: events.append((a, kw)),
                timeout_seconds=8,
            ):
                active += 1
                peak = max(peak, active)
                acquired.append(mode)
                await asyncio.sleep(0.035)
                active -= 1

        await asyncio.gather(*(agent(mode) for mode in [
            "review", "keyword", "cap_stack", "tiktokshop"
        ]))
        assert peak == 1
        assert set(acquired) == {"review", "keyword", "cap_stack", "tiktokshop"}
        assert any(row[0][0] == "scout_network_gate_waited" for row in events)
        assert path.is_file()
    asyncio.run(exercise())




def test_shared_gate_serializes_separate_python_processes(tmp_path):
    """The four real Scouts are separate PIDs, not merely asyncio tasks."""
    lock_path = tmp_path / "cross-process.lock"
    marker = tmp_path / "inside.flag"
    program = (
        "import asyncio,sys,pathlib;"
        "from network_load import scout_network_slot;\\n"
        "async def runner():\\n"
        " async with scout_network_slot('child',path=pathlib.Path(sys.argv[1])):\\n"
        "  pathlib.Path(sys.argv[2]).write_text('locked',encoding='utf-8');\\n"
        "  await asyncio.sleep(1.0)\\n"
        "asyncio.run(runner())"
    ).replace("\\n", "\n")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent) + os.pathsep + env.get("PYTHONPATH", "")
    child = subprocess.Popen(
        [sys.executable, "-c", program, str(lock_path), str(marker)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        for _ in range(80):
            if marker.exists():
                break
            if child.poll() is not None:
                output = child.communicate(timeout=5)
                pytest.fail("Network gate child exited before locking: " + output[1][-800:])
            time.sleep(0.025)
        assert marker.exists()
        start = time.monotonic()

        async def follow():
            async with scout_network_slot("parent", path=lock_path, timeout_seconds=8):
                return True

        assert asyncio.run(follow())
        assert time.monotonic() - start >= 0.55
        assert child.wait(timeout=8) == 0
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)

def test_network_gate_releases_after_exception_and_cancellation(tmp_path):
    async def exercise():
        lock_path = tmp_path / "shared.lock"
        with pytest.raises(ValueError):
            async with scout_network_slot("one", path=lock_path):
                raise ValueError("intentional")
        async with scout_network_slot("two", path=lock_path, timeout_seconds=2):
            pass

        entered = asyncio.Event()
        async def cancelled():
            async with scout_network_slot("cancelled", path=lock_path):
                entered.set()
                await asyncio.sleep(100)

        task = asyncio.create_task(cancelled())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with scout_network_slot("replacement", path=lock_path, timeout_seconds=2):
            pass
    asyncio.run(exercise())


def test_network_gate_wait_can_be_cancelled_without_stealing_owner(tmp_path):
    async def exercise():
        lock_path = tmp_path / "shared.lock"
        async with scout_network_slot("owner", path=lock_path):
            async def other():
                async with scout_network_slot("waiter", path=lock_path):
                    raise AssertionError("waiter must not acquire yet")
            task = asyncio.create_task(other())
            await asyncio.sleep(0.08)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        async with scout_network_slot("next", path=lock_path, timeout_seconds=2):
            pass
    asyncio.run(exercise())


def test_page_ready_waits_for_visible_image_load_and_stable_pin_count():
    class Page:
        def __init__(self):
            self.calls = 0
        async def evaluate(self, _script):
            self.calls += 1
            return {
                "pin_links": 14,
                "images_visible": 8,
                "images_loaded": 1 if self.calls < 3 else 6,
            }

    async def exercise():
        page = Page()
        events = []
        result = await wait_for_visible_pin_images(
            page, phase="search_open", timeout_ms=3_000,
            log=lambda event, **meta: events.append((event, meta)),
        )
        assert result["ready"] is True
        assert result["pin_links"] == 14
        assert result["images_loaded"] == 6
        assert page.calls >= 4
        assert events[-1][0] == "scout_network_page_ready"
    asyncio.run(exercise())


def test_empty_results_do_not_block_indefinitely():
    class Page:
        async def evaluate(self, _script):
            return {"pin_links": 0, "images_visible": 0, "images_loaded": 0}

    async def exercise():
        events = []
        result = await wait_for_visible_pin_images(
            Page(), phase="search_open", timeout_ms=30,
            log=lambda name, **kw: events.append((name, kw)),
        )
        assert result["ready"] is False
        assert events[-1][0] == "scout_network_page_settle_timeout"
    asyncio.run(exercise())


def test_low_bandwidth_cdp_blocks_only_media_and_fonts_preserving_image_cache():
    class Session:
        def __init__(self):
            self.sent = []
        async def send(self, name, options=None):
            self.sent.append((name, options))

    class Context:
        def __init__(self):
            self.pages = [object()]
            self.handler = None
            self.sessions = []
        def on(self, event, callback):
            assert event == "page"
            self.handler = callback
        async def new_cdp_session(self, page):
            session = Session()
            self.sessions.append((page, session))
            return session
        async def route(self, *_):
            raise AssertionError("Playwright routing would disable HTTP image cache!")

    async def exercise():
        ctx = Context()
        await apply_low_bandwidth_routes(ctx)
        assert len(ctx.sessions) == 1
        second = object()
        ctx.handler(second)
        await asyncio.sleep(0.02)
        assert len(ctx.sessions) == 2
        patterns = ctx.sessions[0][1].sent[-1][1]["urls"]
        assert any("mp4" in value for value in patterns)
        assert any("woff" in value for value in patterns)
        assert all("pinimg" not in value for value in patterns)
        assert all(not any(ext in value for ext in ("jpg", "webp", "png")) for value in patterns)
        assert ctx.sessions[0][1].sent[0][0] == "Network.enable"
        assert ctx.sessions[0][1].sent[1][0] == "Network.setBlockedURLs"
    asyncio.run(exercise())


def test_product_cooldown_is_bounded_and_never_claims_next_before_visible_images():
    class Page:
        calls = 0
        async def evaluate(self, _script):
            self.calls += 1
            return {"pin_links": 8, "images_visible": 1, "images_loaded": 1}

    async def exercise():
        page = Page()
        events = []
        await wait_between_products(
            page, pause_seconds=(0.0, 0.0),
            log=lambda name, **meta: events.append((name, meta)),
        )
        assert page.calls >= 3
        assert events[0][0] == "scout_network_page_ready"
        assert events[-1][0] == "scout_network_next_product_pause"
        assert events[-1][1]["delay_ms"] == 0
    asyncio.run(exercise())


def test_gate_path_is_shared_by_sibling_profile_modes(monkeypatch, tmp_path):
    monkeypatch.delenv("RRUGC_SCOUT_NETWORK_GATE_FILE", raising=False)
    result = network_load.scout_network_gate_path()
    assert result.name == ".cam-scout-network-load.lock"
    assert result.parent == Path(network_load.__file__).resolve().parents[2]
    override = tmp_path / "shared" / "network.lock"
    monkeypatch.setenv("RRUGC_SCOUT_NETWORK_GATE_FILE", str(override))
    assert network_load.scout_network_gate_path() == override


def test_hung_chromium_evaluate_respects_real_image_deadline():
    """Browser RPC stalls must not extend a configured 80ms image wait to 24s."""
    class HungPage:
        async def evaluate(self, _script):
            await asyncio.sleep(5)
            raise AssertionError("unreachable")

    async def exercise():
        started = time.monotonic()
        events = []
        result = await wait_for_visible_pin_images(
            HungPage(), phase="pin_detail", timeout_ms=80,
            log=lambda name, **fields: events.append((name, fields)),
        )
        elapsed = time.monotonic() - started
        assert elapsed < 0.45, elapsed
        assert not result["ready"]
        assert result["probe_timeouts"] >= 1
        assert events[-1][0] == "scout_network_page_settle_timeout"
    asyncio.run(exercise())


def test_two_loaded_thumbnails_allow_progress_without_waiting_for_all_images():
    class SlowImagePage:
        calls = 0
        async def evaluate(self, _script):
            self.calls += 1
            return {
                "pin_links": 14, "images_visible": 12, "images_loaded": 2,
            }

    async def exercise():
        page = SlowImagePage()
        result = await wait_for_visible_pin_images(
            page, phase="search_open", timeout_ms=2500,
        )
        assert result["ready"] is True
        assert result["images_loaded"] == 2
        assert page.calls >= 3
    asyncio.run(exercise())


def test_cpu_pressure_governor_paces_four_scout_bursts_without_throttling_idle():
    # Kernel ticks include idle time. CPU 95% means a 4s guard; when the
    # workstation is idle the usual 650ms guard remains unchanged.
    sample = network_load._cpu_busy_percent(
        (100, 500, 500), (110, 600, 600),
    )
    assert sample == pytest.approx(95.0)
    assert network_load._cpu_release_pause_seconds(sample) == 4.0
    assert network_load._cpu_release_pause_seconds(87.0) == 2.0
    assert network_load._cpu_release_pause_seconds(30.0) == (
        network_load.NETWORK_GATE_RELEASE_PAUSE_SECONDS
    )
    assert network_load._cpu_release_pause_seconds(None) == (
        network_load.NETWORK_GATE_RELEASE_PAUSE_SECONDS
    )


def test_invalid_or_wrapped_cpu_counters_fail_open():
    assert network_load._cpu_busy_percent(None, None) is None
    assert network_load._cpu_busy_percent((10, 20, 20), (9, 21, 21)) is None
    assert network_load._cpu_busy_percent((10, 20, 20), (10, 20, 20)) is None


def test_cpu_sampler_is_available_on_windows_without_external_packages():
    sample = network_load._windows_system_cpu_times()
    if os.name == "nt":
        assert sample is not None
        assert len(sample) == 3
        assert all(isinstance(part, int) and part >= 0 for part in sample)
    else:
        assert sample is None
