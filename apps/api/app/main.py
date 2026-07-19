from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.database import init_database
from app.modules.auth.microsoft_router import router as microsoft_auth_router
from app.modules.auth.router import router as auth_router
from app.modules.explorer.router import router as explorer_router
from app.modules.external_ingestion.router import router as external_ingestion_router
from app.modules.metadata.router import router as metadata_router
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
app.include_router(auth_router, prefix="/api")
app.include_router(microsoft_auth_router, prefix="/api")
app.include_router(explorer_router, prefix="/api")
app.include_router(tag_router, prefix="/api")
app.include_router(external_ingestion_router)
app.include_router(metadata_router, prefix="/api")


@app.get("/health")
def health():
    return {"status": "ok"}
