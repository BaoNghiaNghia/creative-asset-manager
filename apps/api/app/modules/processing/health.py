from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class WorkerHealthSnapshot:
    live: bool
    ready: bool
    draining: bool
    worker_id: str
    active_jobs: int
    database_available: bool
    last_poll_at: str | None
    last_successful_claim_at: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "live": self.live,
            "ready": self.ready,
            "draining": self.draining,
            "worker_id": self.worker_id,
            "active_jobs": self.active_jobs,
            "database_available": self.database_available,
            "last_poll_at": self.last_poll_at,
            "last_successful_claim_at": self.last_successful_claim_at,
        }


class WorkerHealthState:
    def __init__(self, worker_id: str):
        self.worker_id = worker_id
        self._lock = threading.Lock()
        self._live = True
        self._startup_complete = False
        self._enabled = False
        self._draining = False
        self._database_available = False
        self._active_jobs = 0
        self._last_poll_at: str | None = None
        self._last_successful_claim_at: str | None = None

    def startup_complete(self, *, enabled: bool, database_available: bool = True) -> None:
        with self._lock:
            self._startup_complete = True
            self._enabled = enabled
            self._database_available = database_available

    def set_database_available(self, available: bool) -> None:
        with self._lock:
            self._database_available = available

    def record_poll(self, *, claimed: bool) -> None:
        now = _utc_iso()
        with self._lock:
            self._last_poll_at = now
            if claimed:
                self._last_successful_claim_at = now

    def set_active_jobs(self, count: int) -> None:
        with self._lock:
            self._active_jobs = max(0, count)

    def start_draining(self) -> None:
        with self._lock:
            self._draining = True

    def stop(self) -> None:
        with self._lock:
            self._live = False
            self._draining = True

    def snapshot(self) -> WorkerHealthSnapshot:
        with self._lock:
            ready = (
                self._live
                and self._startup_complete
                and self._enabled
                and self._database_available
                and not self._draining
            )
            return WorkerHealthSnapshot(
                live=self._live,
                ready=ready,
                draining=self._draining,
                worker_id=self.worker_id,
                active_jobs=self._active_jobs,
                database_available=self._database_available,
                last_poll_at=self._last_poll_at,
                last_successful_claim_at=self._last_successful_claim_at,
            )


class WorkerHealthServer:
    def __init__(self, state: WorkerHealthState, host: str, port: int):
        self.state = state
        self._server = ThreadingHTTPServer((host, port), self._handler_type())
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="worker-health",
            daemon=True,
        )

    def _handler_type(self):
        state = self.state

        class HealthHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                snapshot = state.snapshot()
                if self.path == "/live":
                    status = 200 if snapshot.live else 503
                    body = {"live": snapshot.live}
                elif self.path == "/ready":
                    status = 200 if snapshot.ready else 503
                    body = {"ready": snapshot.ready, "draining": snapshot.draining}
                elif self.path == "/health":
                    status = 200
                    body = snapshot.as_dict()
                else:
                    status = 404
                    body = {"detail": "Not found"}
                encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        return HealthHandler

    @property
    def address(self) -> tuple[str, int]:
        host, port = self._server.server_address[:2]
        return str(host), int(port)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)
