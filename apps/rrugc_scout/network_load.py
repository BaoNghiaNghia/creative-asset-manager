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
NETWORK_GATE_RELEASE_PAUSE_SECONDS = 0.40
VISIBLE_IMAGE_SAMPLE_MS = 300
VISIBLE_IMAGE_READY_TIMEOUT_MS = 6_000
VISIBLE_IMAGE_STABLE_SAMPLES = 2
SCOUT_PRODUCT_COOLDOWN_SECONDS = (2.5, 4.0)
_DEFAULT_LOCK_FILE = ".cam-scout-network-load.lock"
_CONTEXT_ROUTES_INSTALLED: set[int] = set()


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
        try:
            yield
        finally:
            # Prevent a synchronized burst from all waiting browsers.
            await asyncio.sleep(NETWORK_GATE_RELEASE_PAUSE_SECONDS)
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
    """Wait for stable, loaded visible thumbnails; never wait for networkidle.

    Pinterest may keep analytics or infinite-scroll requests outstanding
    forever. Wait only for the first screen's Pin images and a stable Pin-link
    count, with a strict ceiling so empty searches and challenge pages recover.
    """
    started = time.monotonic()
    deadline = started + max(0, timeout_ms) / 1000.0
    previous_count = -1
    stable_samples = 0
    last: dict[str, int | str | bool] = {
        "pin_links": 0, "images_visible": 0, "images_loaded": 0,
        "ready": False, "wait_ms": 0,
    }
    while True:
        try:
            sample = await page.evaluate(
                r"""() => {
                  const pins = document.querySelectorAll('a[href*="/pin/"]').length;
                  const images = Array.from(document.querySelectorAll('img[src*="pinimg.com"]'))
                    .filter(img => {
                      const r = img.getBoundingClientRect();
                      return r.width > 0 && r.height > 0 &&
                        r.top < window.innerHeight && r.bottom > 0;
                    }).slice(0, 16);
                  return {pin_links: pins,
                    images_visible: images.length,
                    images_loaded: images.filter(img => img.complete && img.naturalWidth > 0).length};
                }"""
            )
            if not isinstance(sample, dict):
                break
            pin_links = max(0, int(sample.get("pin_links") or 0))
            visible = max(0, int(sample.get("images_visible") or 0))
            loaded = max(0, int(sample.get("images_loaded") or 0))
            ready_count = min(4, visible)
            ready = pin_links > 0 and visible > 0 and loaded >= ready_count
            if ready and pin_links == previous_count:
                stable_samples += 1
            else:
                stable_samples = 0
            previous_count = pin_links
            last.update(pin_links=pin_links, images_visible=visible,
                        images_loaded=loaded, ready=stable_samples >= VISIBLE_IMAGE_STABLE_SAMPLES)
            if last["ready"]:
                break
        except Exception:
            # A temporary document navigation destroys JS execution context;
            # the existing caller still handles access/network errors.
            break
        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(VISIBLE_IMAGE_SAMPLE_MS / 1000)
    last["wait_ms"] = int((time.monotonic() - started) * 1000)
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
    await wait_for_visible_pin_images(
        page, phase="product_complete", timeout_ms=4_000, log=log,
    )
    cooldown = random.uniform(*pause_seconds)
    if log:
        log("scout_network_next_product_pause", delay_ms=int(cooldown * 1000))
    await asyncio.sleep(cooldown)
