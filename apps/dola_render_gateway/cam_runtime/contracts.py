import hashlib,json
from dataclasses import dataclass
from io import BytesIO
from PIL import Image
from .errors import GatewayError
CONTRACT_VERSION="v1"; ALLOWED_MODELS={"seedance-2.0","seedance-2.5"}; ALIASES={"seedance_v2.0":"seedance-2.0","seedance_2.0":"seedance-2.0","seedance_v2.5":"seedance-2.5","seedance_2.5":"seedance-2.5"}; ALLOWED_RATIOS={"16:9","9:16","1:1","4:3","3:4"}; ALLOWED_DURATIONS={10,15,30}; MIMES={"image/jpeg":".jpg","image/png":".png","image/webp":".webp"}; MAX_REFERENCES=8; MAX_REFERENCE_BYTES=15*1024*1024; MAX_TOTAL_REFERENCE_BYTES=30*1024*1024
@dataclass(frozen=True)
class GenerationRequest: prompt:str; model:str; aspect_ratio:str; duration_seconds:int; references:tuple; suffixes:tuple
def bad(m): raise GatewayError("invalid_request",m)
def parse_metadata(raw,uploads):
 try:data=json.loads(raw)
 except Exception:bad("metadata must be a JSON object")
 if not isinstance(data,dict):bad("metadata must be a JSON object")
 prompt=str(data.get("prompt","")).strip();model=ALIASES.get(str(data.get("model","")).strip().lower(),str(data.get("model","")).strip().lower());ratio=str(data.get("aspect_ratio","")).strip()
 try:duration=int(data.get("duration_seconds"))
 except Exception:bad("duration_seconds must be an integer")
 if not prompt or len(prompt)>4000 or model not in ALLOWED_MODELS or ratio not in ALLOWED_RATIOS or duration not in ALLOWED_DURATIONS:bad("unsupported or missing generation fields")
 if len(uploads)>MAX_REFERENCES:bad("too many reference images")
 total=0;refs=[];suffixes=[]
 for mime,blob in uploads:
  if mime.lower() not in MIMES or not blob:bad("reference must be a non-empty JPEG, PNG, or WEBP image")
  total+=len(blob)
  if len(blob)>MAX_REFERENCE_BYTES or total>MAX_TOTAL_REFERENCE_BYTES:bad("reference images exceed size limits")
  try:
   with Image.open(BytesIO(blob)) as im:im.verify()
  except Exception:bad("reference is not a valid image")
  refs.append(blob);suffixes.append(MIMES[mime.lower()])
 return GenerationRequest(prompt,model,ratio,duration,tuple(refs),tuple(suffixes))
def fingerprint(r):
 return hashlib.sha256(json.dumps({"contract_version":CONTRACT_VERSION,"model":r.model,"prompt":r.prompt,"aspect_ratio":r.aspect_ratio,"duration_seconds":r.duration_seconds,"references":[hashlib.sha256(x).hexdigest() for x in r.references]},sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
