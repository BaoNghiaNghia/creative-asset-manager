"""Optional PostgreSQL commit-notification wakeup for processing workers.

LISTEN/NOTIFY is only a hint. The authoritative claim remains the existing
PostgreSQL SKIP LOCKED transaction, and bounded periodic polling is mandatory:
notifications are not durable, due dates and lease expiry advance without any
database write, and temporary listener failures must not stall workers.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy.engine import make_url

CHANNEL = "cam_processing_queue_wakeup"


def connection_options(database_url: str, *, connect_timeout: int = 5) -> dict[str, Any]:
    """Translate a SQLAlchemy URL to psycopg options without logging secrets."""
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        raise ValueError("PostgreSQL LISTEN requires a PostgreSQL connection")
    options: dict[str, Any] = dict(url.query)
    options.update(url.translate_connect_args(username="user", database="dbname"))
    options["connect_timeout"] = connect_timeout
    return options


class QueueWakeListener:
    """One disposable listener connection per worker, never used for claims."""

    def __init__(
        self,
        database_url: str,
        *,
        accepted_job_types: tuple[str, ...],
        wake_event: threading.Event,
        logger: logging.Logger,
        reconnect_delay_seconds: float = 2.0,
        connect: Callable[..., Any] | None = None,
    ) -> None:
        self._options = connection_options(database_url)
        self._allowed_types = frozenset(accepted_job_types)
        self._wake_event = wake_event
        self._logger = logger
        self._reconnect_delay = reconnect_delay_seconds
        self._connect = connect
        self._stop = threading.Event()
        self._connected = threading.Event()
        self._thread: threading.Thread | None = None
        self.received_events = 0
        self.reconnections = 0
        self._last_error_type: str | None = None

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="cam-claim-notify", daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.5)

    def _run(self) -> None:
        # Lazy import preserves SQLite-based local tests and disabled workers.
        if self._connect is None:
            import psycopg
            self._connect = psycopg.connect

        while not self._stop.is_set():
            try:
                with self._connect(**self._options, autocommit=True) as connection:
                    connection.execute(f"LISTEN {CHANNEL}")
                    self._connected.set()
                    self._last_error_type = None
                    self._wake_event.set()  # Catch any change during reconnect.
                    self._logger.info("worker_claim_notify_connected")
                    while not self._stop.is_set():
                        for notification in connection.notifies(timeout=0.5):
                            if self._stop.is_set():
                                break
                            payload = notification.payload
                            if (
                                payload == "*"
                                or not self._allowed_types
                                or payload in self._allowed_types
                            ):
                                self.received_events += 1
                                self._wake_event.set()
            except Exception as exc:
                # Only the error class is logged; a DSN/password is never logged.
                error_type = type(exc).__name__
                if self._last_error_type != error_type:
                    self._logger.warning(
                        "worker_claim_notify_unavailable",
                        extra={"error_code": error_type},
                    )
                    self._last_error_type = error_type
                self.reconnections += 1
            finally:
                self._connected.clear()
            if not self._stop.is_set():
                self._stop.wait(self._reconnect_delay)
