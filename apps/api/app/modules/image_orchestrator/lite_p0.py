"""Additional Muse Lite P0 endpoints; import only at end of lite_api.py.

Stage a completed attempt using its exact lease even if expired. This does
not grant a new Send. QA reject is immutable, and retry requires independent
operator authorization for a changed workflow and native capability evidence.
"""
from datetime import datetime, timezone
from typing import Annotated
from fastapi import Header, Query, Request, Response
from pydantic import Field
from . import lite_api as a, lite_stage as files

QA_PROTOCOL = 'CAM_MUSE_IMAGE_LITE_QA_STAGE_V2'
MIN_NATIVE = 2000
MAX_ATTEMPTS = 3
REASONS = frozenset(('FAIL_PRINT_TOO_CLOSE_TO_COLLAR','ROLE_01_TRUE_HEM_NOT_VISIBLE',
    'FAIL_TEXTURE_DRIFT','FAIL_ARTWORK_REDRAWN','FAIL_INK_COLOR_SHIFT','FAIL_ALPHA_EDGE_SPILL',
    'FAIL_WHITE_INK_LOSS','FAIL_SOURCE_SUBSTRATE_BAKED','FAIL_PRINT_INTERPRETED_AS_EMBROIDERY',
    'WRONG_GARMENT_OR_SHADE','FAIL_ROLE_FRAMING','FAIL_LIGHTING','FAIL_OCCASION',
    'NATIVE_PUBLICATION_SIZE_NOT_MET','ARTWORK_UNVERIFIED','PLACEMENT_UNVERIFIED'))

def capability(d):
    size=d.get('nativeOutputSize')
    return type(size) is int and MIN_NATIVE<=size<=4000 and a.p.valid_sha(d.get('nativeCapabilityEvidenceSha256'))

def require_stage(job, image_sha=None, width=None, height=None):
    d=job.payload_json; stage=d.get('stage')
    a.check(isinstance(stage,dict) and stage.get('attemptId')==d.get('attemptId')
        and stage.get('epoch')==d.get('epoch') and stage.get('planSha256')==d.get('planSha256')
        and stage.get('workflowSha256')==d.get('workflowSha256'), 'image_stage_required')
    a.check(image_sha is None or stage['imageSha256']==image_sha,'image_stage_sha_mismatch')
    a.check(width is None or (stage['width'],stage['height'])==(width,height),'image_stage_dimensions_mismatch')
    name=files.filename(job.tenant_id,job.id,stage['epoch'],stage['attemptId'],stage['imageSha256'])
    try: raw=files.read(name,stage['imageSha256'],stage['bytes'])
    except files.StageError as exc: a.fail(str(exc),503)
    return stage,raw

class Reject(a.Strict):
    epoch:int=Field(ge=1,strict=True)
    attemptId:str=Field(pattern=a.ATTEMPT)
    imageSha256:str=Field(pattern=a.SHA)
    reviewEvidenceSha256:str=Field(pattern=a.SHA)
    reasons:list[str]=Field(min_length=1,max_length=12)

class Retry(a.Strict):
    rejectionReceiptId:str=Field(pattern=a.SHA)
    workflowSha256:str=Field(pattern=a.SHA)
    nativeCapabilityEvidenceSha256:str=Field(pattern=a.SHA)
    nativeOutputSize:int=Field(ge=MIN_NATIVE,le=4000,strict=True)

class Release(a.Strict):
    rejectionReceiptId:str=Field(pattern=a.SHA)

@a.router.put('/commands/{cid}/stage')
async def stage(cid:str,request:Request,identity:a.Identity,db:a.Db,
        epoch:Annotated[int,Header(alias='X-CAM-Epoch',ge=1)],
        lease:Annotated[str,Header(alias='X-CAM-Image-Lease',pattern=r'^[A-Za-z0-9_-]{48,128}$')],
        sha:Annotated[str,Header(alias='X-CAM-Image-Sha256',pattern=a.SHA)],
        attempt:Annotated[str,Header(alias='X-CAM-Attempt-Id',pattern=a.ATTEMPT)]):
    body=a.Lease(epoch=epoch,lease=lease)
    job=a.get_job(db,identity.tenant,cid)
    a.own(job,identity,body,phases=('started','awaiting_qa'),allow_expired=True)
    a.check(job.payload_json.get('attemptId')==attempt,'image_attempt_mismatch')
    a.check(request.headers.get('content-type','').split(';')[0].strip()=='image/png','image_png_required',415)
    db.rollback()
    raw=bytearray()
    async for chunk in request.stream():
        a.check(len(raw)+len(chunk)<=a.MAX_BYTES,'image_png_too_large',413)
        raw.extend(chunk)
    w,h=a.inspect_png(raw,sha)
    a.lock_tenant(db,identity.tenant);job=a.get_job(db,identity.tenant,cid,lock=True)
    a.own(job,identity,body,phases=('started','awaiting_qa'),allow_expired=True)
    d=job.payload_json
    a.check(d.get('attemptId')==attempt and not d.get('qa') and not d.get('uploadIntentSha256'),'image_attempt_mismatch')
    proof={'epoch':epoch,'attemptId':attempt,'imageSha256':sha,'width':w,'height':h,
           'bytes':len(raw),'planSha256':d['planSha256'],'workflowSha256':d['workflowSha256'],
           'previewOnly':w<MIN_NATIVE,'nativeOriginAttestedByWorker':True}
    proof['stageReceiptId']=a.p.canonical_sha([job.id,proof])
    a.check(d.get('stage') in (None,proof),'immutable_image_stage')
    name=files.filename(job.tenant_id,job.id,epoch,attempt,sha)
    try: files.store(name,sha,bytes(raw))
    except files.StageError as exc: a.fail(str(exc),503)
    # This exact completed Send is now review-only. Renew its lease for QA/
    # upload recovery, not for another render, including after the old expiry.
    expires=a.now()+a.LEASE_TTL
    job.lease_expires_at=datetime.fromtimestamp(expires,timezone.utc)
    a.set_payload(job,phase='awaiting_qa',stage=proof,runAllowed=False,expiresAt=expires)
    db.commit()
    return {'commandId':cid,**proof,'expiresAtEpoch':expires,'status':'awaiting_qa',
            'galleryWritten':False,'videoQueueTouched':False}

@a.router.get('/operator/commands/{cid}/stage')
def read_stage(cid:str,tenant:a.Operator,db:a.Db,
        attemptId:Annotated[str,Query(pattern=a.ATTEMPT)],imageSha256:Annotated[str,Query(pattern=a.SHA)]):
    job=a.get_job(db,tenant,cid);d=job.payload_json
    a.check(d.get('attemptId')==attemptId,'image_attempt_mismatch')
    proof,raw=require_stage(job,imageSha256)
    return Response(raw,media_type='image/png',headers={'Cache-Control':'private, no-store',
        'X-CAM-Image-Sha256':imageSha256,'X-CAM-Stage-Receipt':proof['stageReceiptId']})

@a.router.post('/operator/commands/{cid}/qa-reject')
def reject(cid:str,body:Reject,tenant:a.Operator,db:a.Db):
    a.lock_tenant(db,tenant);job=a.get_job(db,tenant,cid,lock=True);d=job.payload_json
    a.check(job.status=='processing' and d['phase'] in ('awaiting_qa','qa_rejected')
        and d['epoch']==body.epoch and d.get('attemptId')==body.attemptId and not d.get('qa')
        and not d.get('uploadIntentSha256'),'image_rejection_not_allowed')
    a.check(set(body.reasons)<=REASONS and len(set(body.reasons))==len(body.reasons),'image_rejection_reasons_invalid',422)
    proof,_=require_stage(job,body.imageSha256)
    if proof['previewOnly']:
        a.check('NATIVE_PUBLICATION_SIZE_NOT_MET' in body.reasons,'preview_rejection_reason_required',422)
    rejection={'epoch':body.epoch,'attemptId':body.attemptId,'imageSha256':body.imageSha256,
        'stageReceiptId':proof['stageReceiptId'],'reviewEvidenceSha256':body.reviewEvidenceSha256,
        'reasons':sorted(body.reasons)}
    rejection['rejectionReceiptId']=a.p.canonical_sha([job.id,rejection])
    a.check(d.get('rejection') in (None,rejection),'immutable_image_rejection')
    a.set_payload(job,phase='qa_rejected',rejection=rejection,runAllowed=False,leaseHash=None,expiresAt=None)
    job.lease_expires_at=None;db.commit()
    return {'commandId':cid,'status':'rejected',**rejection,'galleryWritten':False,'videoQueueTouched':False}

def rejected_attempt(job,receipt):
    d=job.payload_json;r=d.get('rejection')
    a.check(job.status=='processing' and d['phase']=='qa_rejected' and isinstance(r,dict)
        and r.get('rejectionReceiptId')==receipt and not d.get('qa') and not d.get('uploadIntentSha256'),
        'image_rejection_receipt_required')
    proof,_=require_stage(job,r['imageSha256'])
    a.check(proof['stageReceiptId']==r['stageReceiptId'],'image_rejection_receipt_mismatch')
    return {'stage':proof,'rejection':r,'workflowSha256':d['workflowSha256']}

@a.router.post('/operator/commands/{cid}/retry')
def retry(cid:str,body:Retry,tenant:a.Operator,db:a.Db):
    a.lock_tenant(db,tenant);job=a.get_job(db,tenant,cid,lock=True);d=job.payload_json
    prior=rejected_attempt(job,body.rejectionReceiptId)
    a.check(job.attempt_count<MAX_ATTEMPTS and d['epoch']<3 and not job.cancellation_requested,'image_pilot_retry_limit')
    a.check(body.workflowSha256!=d['workflowSha256'],'revised_image_workflow_required')
    a.check(d.get('cooldownUntilEpoch',0)<=a.now() and a.fence_verified(db,job,a.now()),'image_retry_gate_blocked')
    a.current_asset(db,job);a.no_gallery(db,job)
    history=[*d.get('attemptHistory',[]),prior]
    a.check(len(history)<MAX_ATTEMPTS,'image_pilot_retry_limit')
    a.set_payload(job,phase='prepared',attemptHistory=history,stage=None,rejection=None,qa=None,
        attemptId=None,originalServedSha256=None,leaseHash=None,expiresAt=None,runAllowed=False,
        imageRunnerHeartbeat=None,workflowSha256=body.workflowSha256,
        nativeCapabilityEvidenceSha256=body.nativeCapabilityEvidenceSha256,nativeOutputSize=body.nativeOutputSize)
    job.status='pending';job.claimed_by=None;job.claimed_at=None;job.lease_expires_at=None
    db.commit()
    return {**a.descriptor(job),'imageClaimAllowed':False,'requiresRunnerReady':True,
        'requiresOperatorEnable':True,'videoQueueTouched':False}

@a.router.post('/operator/commands/{cid}/release-rejected')
def release(cid:str,body:Release,tenant:a.Operator,db:a.Db):
    a.lock_tenant(db,tenant);job=a.get_job(db,tenant,cid,lock=True)
    rejected_attempt(job,body.rejectionReceiptId);a.no_gallery(db,job)
    a.set_payload(job,phase='rejected_released',runAllowed=False,leaseHash=None,expiresAt=None)
    job.status='failed';job.completed_at=datetime.now(timezone.utc);job.lease_expires_at=None
    db.commit()
    return {**a.descriptor(job),'legacyReservationRequired':False,'videoQueueTouched':False}

def rejection_status(job,attempt,sha):
    d=job.payload_json
    candidates=[d.get('rejection'),*[x.get('rejection') for x in d.get('attemptHistory',[])]]
    for r in candidates:
        if isinstance(r,dict) and r.get('attemptId')==attempt and r.get('imageSha256')==sha:
            return {'status':'rejected',**r}
    return None
