from fastapi import APIRouter,Request
from fastapi.responses import FileResponse,JSONResponse
from .contracts import parse_metadata,fingerprint
from .errors import GatewayError
router=APIRouter()
def output(r,replay=False):
 d={"generation_id":r["generation_id"],"status":r["state"],"created_at":r["created_at"],"updated_at":r["updated_at"],"idempotent_replay":replay}
 if r["state"]=="completed":d["content_available"]=True
 if r["state"]=="failed":d.update(error_code=r.get("last_error_code"),error_message=r.get("last_error_message"))
 return d
@router.post("/internal/v1/video-generations")
async def submit(request:Request):
 k=request.headers.get("Idempotency-Key","").strip()
 if not k:raise GatewayError("missing_idempotency_key","Idempotency-Key is required")
 if len(k)>200 or any(ord(x)<32 or ord(x)==127 for x in k):raise GatewayError("invalid_idempotency_key","Idempotency-Key is invalid")
 try:form=await request.form();metadata=str(form.get("metadata",""))
 except Exception:raise GatewayError("invalid_request","multipart/form-data is required")
 uploads=[]
 for v in form.getlist("references"):
  if not hasattr(v,"read"):raise GatewayError("invalid_request","references must be files")
  uploads.append((v.content_type or "",await v.read()))
 req=parse_metadata(metadata,uploads);r,replay=await request.app.state.gateway.submit(k,fingerprint(req),req);return output(r,replay)
@router.get("/internal/v1/video-generations/{generation_id}")
async def status(generation_id:str,request:Request):return output(await request.app.state.gateway.status(generation_id))
@router.get("/internal/v1/video-generations/{generation_id}/content")
async def content(generation_id:str,request:Request):
 r=await request.app.state.gateway.status(generation_id);return FileResponse(request.app.state.gateway.content_path(r),media_type="video/mp4",filename="generated.mp4")
def install(app,service):
 app.state.gateway=service
 @app.exception_handler(GatewayError)
 async def handler(_,e):return JSONResponse({"error":{"code":e.code,"message":e.message}},status_code=e.status_code)
 app.include_router(router)
