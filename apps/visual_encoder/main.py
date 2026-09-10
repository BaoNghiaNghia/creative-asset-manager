from __future__ import annotations
import asyncio, base64, binascii, hmac, os, warnings
from contextlib import asynccontextmanager
from io import BytesIO
from fastapi import FastAPI, Header, HTTPException
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel
from siglip import SiglipVisualEncoder

_MAX_BYTES=8_000_000; _MAX_BASE64=4*((_MAX_BYTES+2)//3)+16; _MAX_WIDTH=_MAX_HEIGHT=20_000; _MAX_PIXELS=120_000_000
_lock=asyncio.Lock(); encoder: SiglipVisualEncoder|None=None
class EncodeRequest(BaseModel): image_base64:str
class EncodeTextRequest(BaseModel): text:str
class EncodeResponse(BaseModel): descriptor:dict[str,object]; values:list[float]
def _require(authorization:str|None)->None:
    expected=os.environ.get('VISUAL_ENCODER_INTERNAL_KEY',''); token=authorization[7:] if authorization and authorization.startswith('Bearer ') else ''
    if not expected or not hmac.compare_digest(token,expected): raise HTTPException(401,'unauthorized')
def _decode(value:str)->Image.Image:
    if not value: raise HTTPException(422,'invalid image payload')
    if len(value)>_MAX_BASE64: raise HTTPException(413,'image payload exceeds limit')
    try: raw=base64.b64decode(value,validate=True)
    except (binascii.Error,ValueError) as exc: raise HTTPException(422,'invalid image payload') from exc
    if not raw: raise HTTPException(422,'invalid image payload')
    if len(raw)>_MAX_BYTES: raise HTTPException(413,'image payload exceeds limit')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as probe:
                if probe.format!='JPEG': raise HTTPException(422,'invalid image payload')
                w,h=probe.size
                if w>_MAX_WIDTH or h>_MAX_HEIGHT or w*h>_MAX_PIXELS: raise HTTPException(413,'image payload exceeds limit')
                probe.verify()
            with Image.open(BytesIO(raw)) as opened: opened.load(); return opened.convert('RGB').copy()
    except HTTPException: raise
    except (Image.DecompressionBombError,Image.DecompressionBombWarning): raise HTTPException(413,'image payload exceeds limit')
    except (UnidentifiedImageError,OSError,SyntaxError,ValueError) as exc: raise HTTPException(422,'invalid image payload') from exc
def _response(result):
    d=result.descriptor; return {'descriptor':{'encoder_name':d.encoder_name,'encoder_revision':d.encoder_revision,'embedding_schema_version':d.embedding_schema_version,'dimension':d.dimension,'preprocess_version':d.preprocess_version,'similarity':d.similarity},'values':list(result.values)}
@asynccontextmanager
async def lifespan(_):
    global encoder; encoder=await asyncio.to_thread(SiglipVisualEncoder,os.environ.get('VISUAL_ENCODER_MODEL_PATH','')); yield; encoder=None
app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
@app.get('/live')
def live(): return {'status':'ok'}
@app.get('/ready')
def ready():
    if encoder is None: raise HTTPException(503,'encoder unavailable')
    return {'status':'ok','dimension':encoder.descriptor.dimension,'schema':encoder.descriptor.embedding_schema_version}
@app.post('/v1/encode-image',response_model=EncodeResponse)
async def encode(body:EncodeRequest,authorization:str|None=Header(default=None)):
    _require(authorization)
    if encoder is None: raise HTTPException(503,'encoder unavailable')
    image=_decode(body.image_base64)
    if _lock.locked(): raise HTTPException(503,'encoder busy',headers={'Retry-After':'1'})
    async with _lock: return _response(await asyncio.to_thread(encoder.encode_image,image))
@app.post('/v1/encode-text',response_model=EncodeResponse)
async def encode_text(body:EncodeTextRequest,authorization:str|None=Header(default=None)):
    _require(authorization)
    if encoder is None: raise HTTPException(503,'encoder unavailable')
    text=body.text.strip()
    if not text or len(text)>500: raise HTTPException(422,'text is required')
    if _lock.locked(): raise HTTPException(503,'encoder busy',headers={'Retry-After':'1'})
    async with _lock: return _response(await asyncio.to_thread(encoder.encode_text,text))
