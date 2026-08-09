from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class InventoryWorkerHealth:
    def __init__(self, worker_id: str):
        self.worker_id = worker_id
        self._lock = threading.Lock()
        self._live = True
        self._ready = False
        self._draining = False

    def mark_ready(self, enabled: bool) -> None:
        with self._lock:
            self._ready = enabled and self._live and not self._draining

    def start_draining(self) -> None:
        with self._lock:
            self._draining = True
            self._ready = False

    def stop(self) -> None:
        with self._lock:
            self._live = False
            self._ready = False

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "live": self._live,
                "ready": self._ready,
                "draining": self._draining,
                "worker_id": self.worker_id,
            }


class InventoryWorkerHealthServer:
    def __init__(self, state: InventoryWorkerHealth, host: str, port: int):
        self.state = state
        state_ref = state

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                snapshot = state_ref.snapshot()
                if self.path == "/live":
                    status, body = (200 if snapshot["live"] else 503), {
                        "live": snapshot["live"]
                    }
                elif self.path == "/ready":
                    status, body = (200 if snapshot["ready"] else 503), {
                        "ready": snapshot["ready"]
                    }
                elif self.path == "/health":
                    status, body = 200, snapshot
                else:
                    status, body = 404, {"detail": "Not found"}
                encoded = json.dumps(body, separators=(",", ":")).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self._server = ThreadingHTTPServer((host, port), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="inventory-worker-health",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)
