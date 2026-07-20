from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.database import init_database
from app.modules.ai_governance.router import router as ai_governance_router
from app.modules.auth.microsoft_router import router as microsoft_auth_router
from app.modules.ai_metadata.router import router as ai_metadata_router
from app.modules.auth.router import router as auth_router
from app.modules.explorer.router import router as explorer_router
from app.modules.external_ingestion.router import router as external_ingestion_router
from app.modules.metadata.router import router as metadata_router
from app.modules.processing_policy.router import router as processing_policy_router
from app.modules.asset_details.router import router as asset_details_router
from app.modules.search.router import router as search_router
from app.modules.search.governance_router import router as search_governance_router
from app.modules.tag.router import router as tag_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    get_settings()
    init_database()
    yield


app = FastAPI(title="Creative Asset Manager API", version="0.4.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(ai_governance_router)
app.include_router(ai_metadata_router)
app.include_router(auth_router, prefix="/api")
app.include_router(microsoft_auth_router, prefix="/api")
app.include_router(explorer_router, prefix="/api")
app.include_router(tag_router, prefix="/api")
app.include_router(external_ingestion_router)
app.include_router(metadata_router, prefix="/api")
app.include_router(processing_policy_router)
app.include_router(asset_details_router)
app.include_router(search_router)
app.include_router(search_governance_router)


@app.get("/health")
def health():
    return {"status": "ok"}
