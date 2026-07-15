from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.modules.auth.router import router as auth_router
from app.modules.explorer.router import router as explorer_router
from app.modules.tag.router import router as tag_router

app = FastAPI(title="Creative Asset Manager API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router, prefix="/api")
app.include_router(explorer_router, prefix="/api")
app.include_router(tag_router, prefix="/api")


@app.get("/health")
def health():
    return {"status": "ok"}
