from __future__ import annotations
import asyncio, base64, binascii, os
from contextlib import asynccontextmanager
from io import BytesIO
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from PIL import Image
from app.modules.visual_search.encoder import SiglipVisualEncoder, SiglipEncoderLoadError

_MAX_BYTES=8_000_000
_lock=asyncio.Lock()
encoder: SiglipVisualEncoder | None=None

class EncodeRequest(BaseModel):
    image_base64: str
class EncodeResponse(BaseModel):
    descriptor: dict[str, object]
    values: list[float]

@asynccontextmanager
async def lifespan(_: FastAPI):
    global encoder
    model_path=os.environ.get("VISUAL_ENCODER_MODEL_PATH","")
    try: encoder=await asyncio.to_thread(SiglipVisualEncoder, model_path)
    except SiglipEncoderLoadError: raise
    yield
    encoder=None

app=FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

@app.get("/live")
def live(): return {"status":"ok"}

@app.get("/ready")
def ready():
    if encoder is None: raise HTTPException(503, "encoder unavailable")
    return {"status":"ok","dimension":encoder.descriptor.dimension,"schema":encoder.descriptor.embedding_schema_version}

@app.post("/v1/encode-image", response_model=EncodeResponse)
async def encode(body: EncodeRequest):
    if encoder is None: raise HTTPException(503, "encoder unavailable")
    try: raw=base64.b64decode(body.image_base64, validate=True)
    except (binascii.Error, ValueError) as exc: raise HTTPException(422,"invalid image payload") from exc
    if not raw or len(raw)>_MAX_BYTES: raise HTTPException(413,"image payload exceeds limit")
    try:
        with Image.open(BytesIO(raw)) as source:
            image=source.convert("RGB").copy()
    except Exception as exc: raise HTTPException(422,"invalid image payload") from exc
    if _lock.locked(): raise HTTPException(503,"encoder busy",headers={"Retry-After":"1"})
    async with _lock:
        result=await asyncio.to_thread(encoder.encode_image,image)
    d=result.descriptor
    return {"descriptor":{"encoder_name":d.encoder_name,"encoder_revision":d.encoder_revision,"embedding_schema_version":d.embedding_schema_version,"dimension":d.dimension,"preprocess_version":d.preprocess_version,"similarity":d.similarity},"values":list(result.values)}
