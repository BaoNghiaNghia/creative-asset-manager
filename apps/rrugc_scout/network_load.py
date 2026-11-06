"""Cross-process low-bandwidth coordination for simultaneous Windows Scouts.

Four independent Chrome profiles can generate dozens of overlapping image
requests even though each Scout resolves Pin details sequentially. This module
gates page-navigation and scrolling bursts across all agents sharing a repo,
waits for visible Pinterest thumbnails instead of only DOMContentLoaded, and
blocks nonessential autoplay media/fonts. No proxy or paid API is required.
"""
from __future__ import annotations

import asyncio
import os
import random
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Callable


NETWORK_GATE_POLL_SECONDS = 0.25
NETWORK_GATE_WAIT_SECONDS = 120.0
NETWORK_GATE_RELEASE_PAUSE_SECONDS = 0.65
VISIBLE_IMAGE_SAMPLE_MS = 300
VISIBLE_IMAGE_PROBE_TIMEOUT_SECONDS = 1.25
VISIBLE_IMAGE_READY_TIMEOUT_MS = 6_000
VISIBLE_IMAGE_STABLE_SAMPLES = 2
SCOUT_PRODUCT_COOLDOWN_SECONDS = (2.5, 4.0)
_DEFAULT_LOCK_FILE = ".cam-scout-network-load.lock"
_CONTEXT_ROUTES_INSTALLED: set[int] = set()


def _windows_system_cpu_times() -> tuple[int, int, int] | None:
    """Read global CPU ticks without psutil or a PowerShell child process.

    GetSystemTimes is cheap and supported on the Windows Scout machine.
    The kernel counter includes idle time, so avoid double-counting idle.
    """
    if os.name != "nt":
        return None
    try:
        import ctypes

        class FileTime(ctypes.Structure):
            _fields_ = [
                ("low", ctypes.c_uint32),
                ("high", ctypes.c_uint32),
            ]

        idle, kernel, user = FileTime(), FileTime(), FileTime()
        result = ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user),
        )
        if not result:
            return None
        ticks = lambda row: (int(row.high) << 32) | int(row.low)
        return ticks(idle), ticks(kernel), ticks(user)
    except (OSError, AttributeError, ValueError):
        return None


def _cpu_busy_percent(
    before: tuple[int, int, int] | None,
    after: tuple[int, int, int] | None,
) -> float | None:
    if before is None or after is None:
        return None
    idle = after[0] - before[0]
    total = (after[1] - before[1]) + (after[2] - before[2])
    if idle < 0 or total <= 0:
        return None
    return max(0.0, min(100.0, (1.0 - idle / total) * 100.0))


def _cpu_release_pause_seconds(cpu_percent: float | None) -> float:
    """Reduce *new* image-heavy page bursts when Chrome pegs the host CPU."""
    if cpu_percent is None:
        return NETWORK_GATE_RELEASE_PAUSE_SECONDS
    if cpu_percent >= 95.0:
        return 4.0
    if cpu_percent >= 85.0:
        return 2.0
    return NETWORK_GATE_RELEASE_PAUSE_SECONDS


def prefer_background_scout_priority() -> bool:
    """Keep Scout Chrome launches below interactive work on 4-agent machines.

    This is a scheduling hint, not a hard CPU limit. Call before Playwright
    starts its subprocess tree. Operator can opt out without code changes.
    """
    if os.name != "nt" or os.getenv("RRUGC_SCOUT_LOW_PRIORITY", "1") == "0":
        return False
    try:
        import ctypes
        kernel = ctypes.windll.kernel32
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        return bool(kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x4000))
    except (OSError, AttributeError, ValueError):
        return False


def scout_network_gate_path() -> Path:
    """Shared lock resides in repo root, not in the individual Chrome profiles."""
    custom = os.getenv("RRUGC_SCOUT_NETWORK_GATE_FILE", "").strip()
    return Path(custom).expanduser() if custom else (
        Path(__file__).resolve().parents[2] / _DEFAULT_LOCK_FILE
    )


def _lock_file(handle: Any) -> bool:
    """Try one OS-owned byte lock; the OS releases it after a process crash."""
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock_file(handle: Any) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@asynccontextmanager
async def scout_network_slot(
    phase: str,
    *,
    log: Callable[..., Any] | None = None,
    timeout_seconds: float = NETWORK_GATE_WAIT_SECONDS,
    path: Path | None = None,
) -> AsyncIterator[None]:
    """Limit heavy Pinterest loads to one concurrent burst on this tool machine.

    The filesystem byte lock is shared by the Review, Keyword, Cap Stack and
    TikTokShop processes. Waiting is cooperative and cancellation-safe; it
    never holds Chrome profile locks or sends data over the network.
    """
    lock_path = path or scout_network_gate_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    handle = lock_path.open("a+b", buffering=0)
    owned = False
    try:
        if lock_path.stat().st_size == 0:
            # First creator initializes the byte. Concurrent initialization
            # is harmless: only byte 0 is locked by all processes.
            handle.write(b"0")
            handle.flush()
        while not owned:
            owned = _lock_file(handle)
            if owned:
                break
            duration = time.monotonic() - started
            if duration >= timeout_seconds:
                if log:
                    log("scout_network_gate_timeout", phase=phase, wait_ms=int(duration * 1000))
                raise TimeoutError("Shared Scout network load gate timed out")
            await asyncio.sleep(NETWORK_GATE_POLL_SECONDS + random.uniform(0, 0.12))
        waited_ms = int((time.monotonic() - started) * 1000)
        if log and waited_ms >= 300:
            log("scout_network_gate_waited", phase=phase, wait_ms=waited_ms)
        acquired_at = time.monotonic()
        # Unit-test locks with explicit paths remain deterministic. Production
        # uses the shared default lock and samples CPU before navigation.
        cpu_before = (
            _windows_system_cpu_times()
            if path is None
            and os.getenv("RRUGC_SCOUT_CPU_GOVERNOR", "1") != "0"
            else None
        )
        try:
            yield
        finally:
            held_ms = int((time.monotonic() - acquired_at) * 1000)
            if log and held_ms >= 5_000:
                log("scout_network_gate_slow_hold", phase=phase,
                    held_ms=held_ms, wait_ms=waited_ms)
            busy = _cpu_busy_percent(cpu_before, _windows_system_cpu_times())
            pause = _cpu_release_pause_seconds(busy)
            if log and busy is not None and busy >= 85:
                log("scout_network_cpu_backpressure", phase=phase,
                    cpu_percent=round(busy, 1), delay_ms=int(pause * 1000),
                    held_ms=held_ms, wait_ms=waited_ms)
            # Keep the shared lock for a bounded cooling interval, so all
            # four Chrome profiles do not decode images at once under CPU
            # saturation. The CAM lease heartbeat runs on a separate task.
            await asyncio.sleep(pause)
    finally:
        if owned:
            try:
                _unlock_file(handle)
            finally:
                handle.close()
        else:
            handle.close()


NONESSENTIAL_ASSET_PATTERNS = [
    "*.mp4*", "*.webm*", "*.m3u8*", "*.mov*",
    "*.woff2*", "*.woff*", "*.ttf*", "*.otf*",
]


async def apply_low_bandwidth_routes(
    context: Any, *, log: Callable[..., Any] | None = None,
) -> None:
    """Block only large autoplay media/fonts without disabling Chrome HTTP cache.

    Playwright context.route("**/*") disables browser caching, meaning four
    profiles reload every Pin thumbnail. Chrome DevTools Network.setBlockedURLs
    is target-scoped and preserves normal image/cache behavior.
    """
    attached: set[int] = set()
    sessions: list[Any] = []

    async def attach(page: Any) -> None:
        key = id(page)
        if key in attached:
            return
        attached.add(key)
        try:
            session = await context.new_cdp_session(page)
            await session.send("Network.enable")
            await session.send(
                "Network.setBlockedURLs",
                {"urls": NONESSENTIAL_ASSET_PATTERNS},
            )
            sessions.append(session)
            if log:
                log("scout_network_low_bandwidth_enabled",
                    blocked_types="video,font", thumbnail_cache_preserved=True)
        except Exception as exc:
            # Do not reject the login session if CDP is unavailable.
            if log:
                log("scout_network_low_bandwidth_unavailable",
                    error_type=type(exc).__name__)

    def on_new_page(page: Any) -> None:
        asyncio.create_task(attach(page))

    on = getattr(context, "on", None)
    if callable(on):
        on("page", on_new_page)
    for page in list(getattr(context, "pages", ())):
        await attach(page)


async def wait_for_visible_pin_images(
    page: Any,
    *,
    phase: str,
    timeout_ms: int = VISIBLE_IMAGE_READY_TIMEOUT_MS,
    log: Callable[..., Any] | None = None,
) -> dict[str, int | str | bool]:
    """Bound image readiness even if Chromium's evaluate RPC gets stuck.

    A slow Pinterest renderer can make a *single* page.evaluate exceed the
    entire configured 2–6s image budget. The timeout therefore surrounds
    every browser RPC, not just sleeps between polls. Missing thumbnails are
    soft pressure: downstream candidate validation still checks images.
    """
    started = time.monotonic()
    deadline = started + max(0, timeout_ms) / 1000.0
    previous_count = -1
    stable_samples = 0
    probe_timeouts = 0
    last: dict[str, int | str | bool] = {
        "pin_links": 0, "images_visible": 0, "images_loaded": 0,
        "ready": False, "wait_ms": 0, "probe_timeouts": 0,
    }
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            sample = await asyncio.wait_for(
                page.evaluate(
                    r"""() => {
                      const pins = document.querySelectorAll('a[href*="/pin/"]').length;
                      const images = Array.from(document.querySelectorAll('img[src*="pinimg.com"]'))
                        .filter(img => {
                          const r = img.getBoundingClientRect();
                          return r.width > 0 && r.height > 0 &&
                            r.top < window.innerHeight && r.bottom > 0;
                        }).slice(0, 12);
                      return {pin_links: pins,
                        images_visible: images.length,
                        images_loaded: images.filter(img => img.complete && img.naturalWidth > 0).length};
                    }"""
                ),
                timeout=min(VISIBLE_IMAGE_PROBE_TIMEOUT_SECONDS, remaining),
            )
        except asyncio.TimeoutError:
            probe_timeouts += 1
            continue
        except Exception:
            # Navigation may have replaced the document. The caller handles
            # hard browser failures; a readiness check must not stall others.
            break
        if not isinstance(sample, dict):
            break
        pin_links = max(0, int(sample.get("pin_links") or 0))
        visible = max(0, int(sample.get("images_visible") or 0))
        loaded = max(0, int(sample.get("images_loaded") or 0))
        # A broken/offscreen image must not block the entire next Scout phase.
        # Pin detail and eventual candidate submission still validate URLs.
        ready_count = min(2, visible)
        ready = pin_links > 0 and visible > 0 and loaded >= ready_count
        if ready and pin_links == previous_count:
            stable_samples += 1
        else:
            stable_samples = 0
        previous_count = pin_links
        last.update(pin_links=pin_links, images_visible=visible,
                    images_loaded=loaded,
                    ready=stable_samples >= VISIBLE_IMAGE_STABLE_SAMPLES)
        if last["ready"]:
            break
        await asyncio.sleep(min(
            VISIBLE_IMAGE_SAMPLE_MS / 1000,
            max(0.0, deadline - time.monotonic()),
        ))
    last["wait_ms"] = int((time.monotonic() - started) * 1000)
    last["probe_timeouts"] = probe_timeouts
    if log:
        log(
            "scout_network_page_ready" if last["ready"] else "scout_network_page_settle_timeout",
            phase=phase, **last,
        )
    return last


async def wait_between_products(
    page: Any,
    *,
    log: Callable[..., Any] | None = None,
    pause_seconds: tuple[float, float] = SCOUT_PRODUCT_COOLDOWN_SECONDS,
) -> None:
    """Drain the previous product's visible requests before claiming the next."""
    readiness = await wait_for_visible_pin_images(
        page, phase="product_complete", timeout_ms=4_000, log=log,
    )
    cooldown = random.uniform(*pause_seconds)
    if not readiness["ready"]:
        # When thumbnails remain congested, back off the next claim rather
        # than creating a four-browser retry storm. Fully loaded pages retain
        # the previous pacing without unnecessary slowdown.
        cooldown += random.uniform(1.5, 3.0)
    if log:
        log("scout_network_next_product_pause",
            delay_ms=int(cooldown * 1000),
            image_ready=bool(readiness["ready"]),
            image_wait_ms=readiness["wait_ms"])
    await asyncio.sleep(cooldown)
