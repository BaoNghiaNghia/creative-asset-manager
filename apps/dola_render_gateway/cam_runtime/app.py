from __future__ import annotations

import hmac
import importlib
import os
import sys
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import RuntimeSettings, load_settings


def _configure_upstream(settings: RuntimeSettings) -> None:
    os.environ.update({
        "DOLA_HOST": settings.host,
        "DOLA_PORT": str(settings.port),
        "DOLA_DB_PATH": str(settings.paths.tasks_db),
        "DOLA_POOL_DB_PATH": str(settings.paths.pool_usage_db),
        "DOLA_PROFILE_DIR": str(settings.paths.profiles_dir),
        "DOLA_DOWNLOAD_DIR": str(settings.paths.downloads_dir),
        "DOLA_ARTIFACTS_DIR": str(settings.paths.artifacts_dir),
        "DOLA_RUNTIME_TMP_DIR": str(settings.paths.tmp_dir),
        "DOLA_EXTENSION_DIR": str(settings.paths.source_root / "extensions" / "dola30"),
    })


def _load_upstream(settings: RuntimeSettings) -> FastAPI:
    source = str(settings.paths.source_root)
    if not settings.paths.source_root.is_dir():
        raise RuntimeError("Vendored Dola upstream source directory does not exist.")
    _configure_upstream(settings)
    if source not in sys.path:
        sys.path.insert(0, source)
    module = importlib.import_module("server")
    return module.app


def create_app(
    settings: RuntimeSettings | None = None,
    *,
    upstream_app: FastAPI | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    settings.paths.initialize()
    delegated = upstream_app or _load_upstream(settings)
    app = FastAPI(title="CAM Dola Render Gateway", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def internal_bearer_auth(request: Request, call_next: Callable):
        if request.url.path == "/health/live":
            return await call_next(request)
        authorization = request.headers.get("Authorization", "")
        expected = "Bearer " + settings.internal_api_key
        if not hmac.compare_digest(authorization, expected):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready() -> dict[str, str]:
        return {"status": "ok"}

    app.mount("/", delegated)
    return app


def main() -> None:
    settings = load_settings()
    app = create_app(settings)
    import uvicorn
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
