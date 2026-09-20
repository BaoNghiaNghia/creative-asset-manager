"""Process-local Phase 4E circuit breaker for signed Worker delivery.

This guard never mutates the persisted runtime toggle. When Worker/R2 health is
uncertain it fails open to the existing provider stream by withholding the CDN
redirect for the current API process.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock

import httpx

from app.core.config import Settings
from app.modules.video_cache.delivery import SignedVideoDelivery


@dataclass(frozen=True)
class GuardDecision:
    action: str


class VideoDeliveryCircuitBreaker:
    def __init__(self, *, clock: Callable[[], float] | None = None):
        self._clock = clock or time.monotonic
        self._lock = Lock()
        self._consecutive_failures = 0
        self._open_until = 0.0
        self._last_probe_at: float | None = None
        self._probe_in_flight = False
        self._healthy_sample = False

    def before_candidate(self, settings: Settings) -> GuardDecision:
        if not settings.VIDEO_CDN_DELIVERY_GUARD_ENABLED:
            return GuardDecision("pass")
        now = self._clock()
        with self._lock:
            if self._open_until > now:
                return GuardDecision("fallback")
            if self._probe_in_flight:
                return GuardDecision("pass" if self._healthy_sample else "fallback")
            cooldown_expired = self._open_until > 0 and self._open_until <= now
            due = (
                self._last_probe_at is None
                or now - self._last_probe_at >= settings.VIDEO_CDN_DELIVERY_GUARD_PROBE_INTERVAL_SECONDS
            )
            if cooldown_expired or self._consecutive_failures > 0 or due:
                self._probe_in_flight = True
                return GuardDecision("probe")
            return GuardDecision("pass")

    def complete_probe(self, settings: Settings, *, success: bool) -> bool:
        """Record a probe. Returns True only when this failure opened the circuit."""
        now = self._clock()
        opened = False
        with self._lock:
            self._probe_in_flight = False
            self._last_probe_at = now
            if success:
                self._consecutive_failures = 0
                self._open_until = 0.0
                self._healthy_sample = True
            else:
                self._healthy_sample = False
                self._consecutive_failures += 1
                if self._consecutive_failures >= settings.VIDEO_CDN_DELIVERY_GUARD_FAILURE_THRESHOLD:
                    self._open_until = now + settings.VIDEO_CDN_DELIVERY_GUARD_COOLDOWN_SECONDS
                    opened = True
        return opened

    def snapshot(self, settings: Settings) -> dict[str, object]:
        now = self._clock()
        with self._lock:
            if not settings.VIDEO_CDN_DELIVERY_GUARD_ENABLED:
                state = "disabled"
            elif self._open_until > now:
                state = "open"
            elif self._probe_in_flight:
                state = "probing"
            elif self._consecutive_failures > 0:
                state = "degraded"
            elif self._healthy_sample:
                state = "closed"
            else:
                state = "unverified"
            remaining = max(0.0, self._open_until - now)
            return {
                "scope": "process_local",
                "enabled": bool(settings.VIDEO_CDN_DELIVERY_GUARD_ENABLED),
                "state": state,
                "consecutive_failures": self._consecutive_failures,
                "failure_threshold": settings.VIDEO_CDN_DELIVERY_GUARD_FAILURE_THRESHOLD,
                "probe_interval_seconds": settings.VIDEO_CDN_DELIVERY_GUARD_PROBE_INTERVAL_SECONDS,
                "cooldown_seconds": settings.VIDEO_CDN_DELIVERY_GUARD_COOLDOWN_SECONDS,
                "open_remaining_seconds": round(remaining, 3),
            }

    def reset_for_test(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._open_until = 0.0
            self._last_probe_at = None
            self._probe_in_flight = False
            self._healthy_sample = False


def probe_signed_video_head(
    ticket: SignedVideoDelivery,
    *,
    expected_size: int,
    timeout_seconds: float,
) -> bool:
    """HEAD a signed Worker URL without redirects, proxy env, or response body."""
    try:
        with httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = client.head(
                ticket.url,
                headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
            )
    except httpx.HTTPError:
        return False
    content_type = response.headers.get("Content-Type", "")
    return (
        response.status_code == 200
        and response.headers.get("Content-Length") == str(expected_size)
        and content_type.startswith("video/")
        and response.headers.get("Accept-Ranges") == "bytes"
    )


VIDEO_DELIVERY_GUARD = VideoDeliveryCircuitBreaker()
