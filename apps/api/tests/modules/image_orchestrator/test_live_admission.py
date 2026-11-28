"""Operator policy and exact-PNG reviewer regression without provider calls."""
import hashlib
import io
from types import SimpleNamespace
import pytest
from PIL import Image
from app.modules.image_orchestrator import approvals,policy as p
from app.modules.image_orchestrator.live_review import comparison_board,ReviewContext
from app.modules.image_orchestrator.operator_admission import Admit,validate_request

def evidence():
    return {"scope":"single_pack","resourceEvidenceSha256":"a"*64,
        "cronNativeEvidenceSha256":"b"*64,"driveEvidenceSha256":"c"*64,
        "backupReceiptSha256":"d"*64,"restoreEvidenceSha256":None,
        "backupOnlyCanary":True,"serverOnlyLegacyFence":True,
        "legacyClientDrainVerified":False,"nativeOutputPx":2000,"previewOnlyCanary":False}

def test_operator_scope_never_inferred_from_hashes_or_resolution():
    e=evidence()
    assert approvals.execution_evidence_valid(e)
    assert not approvals.execution_evidence_valid({**e,"backupOnlyCanary":False})
    assert not approvals.execution_evidence_valid({**e,"serverOnlyLegacyFence":False})
    assert not approvals.execution_evidence_valid({**e,"nativeOutputPx":1600})
    assert approvals.execution_evidence_valid({**e,"nativeOutputPx":1600,"previewOnlyCanary":True})
    assert not approvals.execution_evidence_valid({**e,"scope":"all"})

def test_comparison_board_retains_original_ink_and_candidate_pixels():
    buf=io.BytesIO()
    source=Image.new("RGBA",(20,30),(0,0,0,0))
    source.paste((255,255,255,255),(3,4,13,24))
    source.save(buf,format="PNG");raw=buf.getvalue()
    buf=io.BytesIO();Image.new("RGB",(32,32),(20,30,40)).save(buf,format="PNG")
    candidate=buf.getvalue()
    ctx=SimpleNamespace(artwork_sha=hashlib.sha256(raw).hexdigest(),
        image_sha=hashlib.sha256(candidate).hexdigest(),width=32,height=32)
    board,size,ink=comparison_board(raw,candidate,ctx)
    assert ink==(10,20) and size==(66,32)
    with Image.open(io.BytesIO(board)) as img:
        assert img.getpixel((0,0))==(255,255,255)
        assert img.getpixel((34,0))==(20,30,40)
    with pytest.raises(p.Conflict):comparison_board(raw,candidate+b"changed",ctx)
