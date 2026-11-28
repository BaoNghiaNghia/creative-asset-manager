"""P2b authenticated image-only mutation routes.

Fail closed until CAM installs independently verified plan, QA and storage
adapters. Merely setting a feature flag cannot admit production work.
"""
from __future__ import annotations

import hashlib
import io
import re
import time
from typing import Annotated, Callable, Awaitable, Literal
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request, Response, Header, Path, Query
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

from . import policy as p
from .commands import CommandService, Runtime, Scope, UploadTicket
from .storage import SavedOutput
from .central_read_api import Identity, Database, read_api_enabled
from app.modules.render_resources.service import ResourceConflict

MAX_BYTES=32*1024*1024


@dataclass(frozen=True)
class Adapters:
    runtime: Runtime
    # Must resolve QA/source proof, store through CAM's shared Drive adapter
    # and return server-verified receipt, not a renderer's claimed upload.
    store: Callable[[UploadTicket,bytes],Awaitable[SavedOutput]]


def get_adapters() -> Adapters:
    raise HTTPException(503,"image_execution_adapters_unverified")


class RedactedRoute(APIRoute):
    def get_route_handler(self):
        original=super().get_route_handler()
        async def handler(request):
            try:
                return await original(request)
            except RequestValidationError:
                # Pydantic errors can include lease/body input. Never reflect it.
                raise HTTPException(422,"invalid_image_request") from None
        return handler


router=APIRouter(prefix="/api/v1/muse-bridge",tags=["image-orchestrator"],
                 dependencies=[Depends(read_api_enabled)],route_class=RedactedRoute)
Backend=Annotated[Adapters,Depends(get_adapters)]
PackHeader=Annotated[str,Header(alias="X-CAM-Pack-Id",pattern=r"^[a-f0-9]{64}$")]
SkuHeader=Annotated[str,Header(alias="X-CAM-Garment-Sku",pattern=r"^(C1717|G500|G180|G185)$")]
CommandPath=Annotated[str,Path(pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$")]
ListingPath=Annotated[str,Path(pattern=r"^[0-9]{5,20}$")]
CommandQuery=Annotated[str,Query(pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$")]
RolePath=Annotated[str,Path(pattern=r"^(0?[1-9]|1[0-3])$")]


class LeaseBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    lease: SecretStr
    epoch: int=Field(ge=1,strict=True)
    def token(self):
        return check_token(self.lease.get_secret_value())


class HeartbeatBody(LeaseBody):
    state: Literal["started","researching","collecting","validating","uploading","blocked","active","idle","stopping"]
    note: str=Field(default="",max_length=180)


class CompleteBody(LeaseBody):
    outcome: Literal["success","partial","failed"]
    notes: str=Field(default="",max_length=800)
    roles: dict[str,Literal["verified","pending","held","hold","failed","in_flight"]]|None=None


class RoleStartBody(LeaseBody):
    attemptId: str=Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")


def check_token(token):
    if not isinstance(token,str) or not re.fullmatch(r"[A-Za-z0-9_-]{48,128}",token):
        raise HTTPException(422,"invalid_image_lease")
    return token


def perform(db, fn):
    try:
        result=fn()
        db.commit()
        return result
    except (p.Conflict,ResourceConflict) as exc:
        db.rollback()
        code=str(exc)
        if not re.fullmatch(r"[a-z0-9_]{1,100}",code):
            code="image_transition_rejected"
        raise HTTPException(404 if code.endswith("not_found") else 409,code) from None
    except Exception:
        db.rollback()
        raise


def service(db,identity,backend):
    return CommandService(db,identity,backend.runtime)


def scope(command,pack,sku,listing=None):
    return Scope(command,pack,sku,listing)


def now():
    return int(time.time())


def private(response):
    response.headers["Cache-Control"]="private, no-store"


@router.post("/image-commands/{command_id}/claim")
def claim(command_id: CommandPath, pack: PackHeader, sku: SkuHeader,
          response: Response,identity: Identity,db: Database,backend: Backend,
          skill_sha: Annotated[str,Header(alias="X-CAM-Skill-Sha256")]):
    private(response)
    return perform(db,lambda:service(db,identity,backend).claim(
        scope(command_id,pack,sku),now(),skill_sha))


@router.post("/jobs/{listing_id}/images/claim")
def claim_image(listing_id: ListingPath,commandId: CommandQuery,
                pack: PackHeader,sku: SkuHeader,body: LeaseBody,
                response: Response,identity: Identity,db: Database,backend: Backend):
    private(response)
    return perform(db,lambda:service(db,identity,backend).claim_image(
        scope(commandId,pack,sku,listing_id),body.epoch,body.token(),now()))


@router.post("/image-commands/{command_id}/heartbeat")
def heartbeat(command_id: CommandPath,pack: PackHeader,sku: SkuHeader,
              body: HeartbeatBody,response: Response,identity: Identity,db: Database,backend: Backend):
    private(response)
    return perform(db,lambda:service(db,identity,backend).heartbeat(
        scope(command_id,pack,sku),body.epoch,body.token(),now()))


@router.post("/jobs/{listing_id}/images/{role}/start")
def start_role(listing_id: ListingPath,role: RolePath,commandId: CommandQuery,
               pack: PackHeader,sku: SkuHeader,body: RoleStartBody,
               response: Response,identity: Identity,db: Database,backend: Backend):
    private(response)
    return perform(db,lambda:service(db,identity,backend).start(
        scope(commandId,pack,sku,listing_id),body.epoch,body.token(),int(role),body.attemptId,now()))


@router.post("/image-commands/{command_id}/complete")
def complete(command_id: CommandPath,pack: PackHeader,sku: SkuHeader,
             body: CompleteBody,response: Response,identity: Identity,db: Database,backend: Backend):
    private(response)
    return perform(db,lambda:service(db,identity,backend).finish(
        scope(command_id,pack,sku),body.epoch,body.token(),now(),body.outcome,roles=body.roles))


@router.post("/image-commands/{command_id}/yield")
def yield_command(command_id: CommandPath,pack: PackHeader,sku: SkuHeader,
                  body: LeaseBody,response: Response,identity: Identity,db: Database,backend: Backend):
    private(response)
    return perform(db,lambda:service(db,identity,backend).yield_unsent(
        scope(command_id,pack,sku),body.epoch,body.token(),now()))


def inspect_png(data: bytes, claimed_sha: str):
    if (not p.valid_sha(claimed_sha) or not 57<=len(data)<=MAX_BYTES
        or hashlib.sha256(data).hexdigest()!=claimed_sha
        or not data.startswith(b"\x89PNG\r\n\x1a\n")):
        raise HTTPException(422,"image_png_sha_mismatch")
    try:
        with Image.open(io.BytesIO(data)) as img:
            w,h=img.size
            if (img.format!="PNG" or img.mode not in ("RGB","RGBA","P")
                or getattr(img,"is_animated",False) or w!=h or not 1600<=w<=4096
                or w*h>16_000_000):
                raise ValueError("invalid native PNG")
            img.verify()
        with Image.open(io.BytesIO(data)) as img:
            img.load()
    except (OSError,ValueError,Image.DecompressionBombError,UnidentifiedImageError):
        raise HTTPException(422,"invalid_native_png") from None
    return w,h


@router.post("/jobs/{listing_id}/images/{role}")
async def upload(listing_id: ListingPath,role: RolePath,commandId: CommandQuery,
                 pack: PackHeader,sku: SkuHeader,request: Request,response: Response,
                 identity: Identity,db: Database,backend: Backend,
                 image_token: Annotated[str,Header(alias="X-Muse-Lease")],
                 epoch: Annotated[int,Header(alias="X-CAM-Epoch",ge=1)],
                 attempt: Annotated[str,Header(alias="X-CAM-Attempt-Id",pattern=r"^[A-Za-z0-9_-]{1,128}$")],
                 sha: Annotated[str,Header(alias="X-CAM-Image-Sha256")],
                 artwork: Annotated[str,Header(alias="X-CAM-Artwork-SHA256")],
                 placement: Annotated[str,Header(alias="X-CAM-Placement-Lock-Sha256")],
                 qa_id: Annotated[str,Header(alias="X-CAM-QA-Receipt-Id",pattern=r"^[A-Za-z0-9_-]{1,128}$")],
                 source_sha: Annotated[str,Header(alias="X-CAM-Source-Sha256")],
                 skill_sha: Annotated[str,Header(alias="X-CAM-Skill-Sha256")],
                 header_role: Annotated[str,Header(alias="X-CAM-Image-Role")],
                 batch_id: Annotated[str|None,Header(alias="X-CAM-Batch-Id",pattern=r"^[a-f0-9]{32}$")]=None,
                 output_index: Annotated[int|None,Header(alias="X-CAM-Output-Index",ge=0,le=12)]=None,
                 generation_method: Annotated[Literal["native_imagegen","hybrid_compositor"]|None,Header(alias="X-CAM-Generation-Method")]=None,
                 qa_record_sha: Annotated[str|None,Header(alias="X-CAM-QA-Record-Sha256",pattern=r"^[a-f0-9]{64}$")]=None):
    private(response)
    token=check_token(image_token)
    if header_role not in (f"{int(role):02d}",str(int(role))):
        raise HTTPException(422,"image_role_mismatch")
    if request.headers.get("content-type","").split(";")[0].lower()!="image/png":
        raise HTTPException(415,"image_png_required")
    data=bytearray()
    async for chunk in request.stream():
        if len(data)+len(chunk)>MAX_BYTES:
            raise HTTPException(413,"image_upload_too_large")
        data.extend(chunk)
    from starlette.concurrency import run_in_threadpool
    png=bytes(data)
    w,h=await run_in_threadpool(inspect_png,png,sha)
    command_scope=scope(commandId,pack,sku,listing_id)
    prepared=perform(db,lambda:service(db,identity,backend).prepare_upload(
        command_scope,epoch,token,int(role),attempt,now(),sha,w,h,artwork,placement,qa_id,
        source_sha,skill_sha,batch_id=batch_id,output_index=output_index,
        generation_method=generation_method,qa_record_sha=qa_record_sha))
    if prepared["alreadyPrepared"]:
        # Caller must recover via image-status. An ACK retry never writes a second file.
        raise HTTPException(409,"image_upload_already_prepared")
    ticket=prepared["ticket"]
    # No DB locks during Drive I/O. If the call fails or the lease expires,
    # the durable upload intent remains unresolved for safe reconciliation.
    try:
        saved=await backend.store(ticket,png)
        if not isinstance(saved,SavedOutput):
            raise ValueError('invalid storage adapter result')
    except Exception:
        raise HTTPException(503,"image_storage_reconciliation_required") from None
    return perform(db,lambda:service(db,identity,backend).accept_storage(
        command_scope,token,now(),ticket,saved.receipt,project=saved.project))