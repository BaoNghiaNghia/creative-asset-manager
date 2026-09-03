from contextlib import asynccontextmanager

from app.core.environment import load_development_environment

load_development_environment()

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.core.config import Settings, get_settings
from app.core.database import dispose_database, init_database
from app.core.health import readiness_report
from app.modules.ai_governance.router import router as ai_governance_router
from app.modules.ai_operations.router import router as ai_operations_router
from app.modules.ai_operations.control_router import router as ai_operations_control_router
from app.modules.ai_metadata.router import capabilities_router, router as ai_metadata_router
from app.modules.ai_metadata.bulk_router import router as ai_metadata_bulk_router
from app.modules.application_logs.admin_router import router as application_log_admin_router
from app.modules.application_logs.router import router as application_log_router
from app.modules.asset_details.router import router as asset_details_router
from app.modules.auth.microsoft_router import router as microsoft_auth_router
from app.modules.auth.router import router as auth_router
from app.modules.authorization.admin_router import router as access_management_router
from app.modules.authorization.router import router as authorization_router
from app.modules.explorer.router import router as explorer_router
from app.modules.external_ingestion.router import router as external_ingestion_router
from app.modules.metadata.router import router as metadata_router
from app.modules.inventory.router import router as inventory_router
from app.modules.image_generation.router import router as image_generation_router
from app.modules.processing_policy.router import router as processing_policy_router
from app.modules.search.governance_router import router as search_governance_router
from app.modules.search.router import router as search_router
from app.modules.video_search.router import router as video_search_router
from app.modules.search.shadow_runtime import SHADOW_SEARCH
from app.modules.search.runtime import API_SEARCH_INDEX_POOL, SEARCH_SUGGESTION_CACHE
from app.modules.tag.router import router as tag_router
from app.providers.google.drive import create_stream_client


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = _app.state.settings
    google_drive_stream_client = create_stream_client()
    _app.state.google_drive_stream_client = google_drive_stream_client
    shadow_started = False
    try:
        init_database(settings)
        SHADOW_SEARCH.start()
        shadow_started = True
        yield
    finally:
        try:
            if shadow_started:
                await SHADOW_SEARCH.shutdown(
                    settings.SEARCH_SHADOW_SHUTDOWN_TIMEOUT_MS / 1000
                )
        finally:
            try:
                await google_drive_stream_client.aclose()
                await API_SEARCH_INDEX_POOL.aclose_current_loop()
                SEARCH_SUGGESTION_CACHE.clear()
            finally:
                _app.state.google_drive_stream_client = None
                dispose_database()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    # Lifespan uses this exact resolved Settings instance.
    api = FastAPI(
        title="Creative Asset Manager API",
        version=settings.APP_VERSION,
        lifespan=lifespan,
        docs_url="/docs" if settings.API_DOCS_ENABLED else None,
        redoc_url="/redoc" if settings.API_DOCS_ENABLED else None,
        openapi_url="/openapi.json" if settings.API_DOCS_ENABLED else None,
    )
    api.state.settings = settings
    api.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=list(settings.trusted_hosts),
    )
    if settings.cors_allowed_origins:
        api.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_allowed_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
        )

    if settings.PROXY_HEADERS_ENABLED:
        api.add_middleware(
            ProxyHeadersMiddleware,
            trusted_hosts=list(settings.proxy_trusted_ips),
        )

    api.include_router(ai_governance_router)
    api.include_router(ai_operations_router)
    api.include_router(ai_operations_control_router)
    api.include_router(ai_metadata_router)
    api.include_router(ai_metadata_bulk_router)
    api.include_router(capabilities_router)
    api.include_router(application_log_router)
    api.include_router(application_log_admin_router)
    api.include_router(auth_router, prefix="/api")
    api.include_router(microsoft_auth_router, prefix="/api")
    api.include_router(authorization_router)
    api.include_router(access_management_router)
    api.include_router(explorer_router, prefix="/api")
    api.include_router(tag_router, prefix="/api")
    api.include_router(external_ingestion_router)
    api.include_router(metadata_router, prefix="/api")
    api.include_router(processing_policy_router)
    api.include_router(asset_details_router)
    api.include_router(image_generation_router)
    api.include_router(search_router)
    api.include_router(video_search_router)
    api.include_router(search_governance_router)
    # Inventory runtime execution remains default-off, but its authenticated
    # tenant configuration and status routes must stay reachable so an
    # administrator can configure credentials before automation is enabled.
    api.include_router(inventory_router)

    @api.get("/live")
    def live():
        return {"status": "ok"}

    @api.get("/health")
    def health():
        return {"status": "ok"}

    @api.get("/ready")
    def ready():
        result = readiness_report(settings)
        return JSONResponse(
            status_code=result.status_code,
            content=result.payload,
        )

    @api.get("/version")
    def version():
        return {
            "version": settings.APP_VERSION,
            "commit": settings.BUILD_COMMIT,
        }

    return api


app = create_app()
