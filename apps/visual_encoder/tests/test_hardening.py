from __future__ import annotations
import io, os, sys
from pathlib import Path
import pytest
from fastapi import HTTPException
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
API=ROOT.parent/'api'
for value in (str(ROOT),str(API)):
    if value not in sys.path: sys.path.insert(0,value)
import main

def test_bearer_auth_fails_closed_and_accepts_exact_key(monkeypatch):
    monkeypatch.delenv('VISUAL_ENCODER_INTERNAL_KEY',raising=False)
    with pytest.raises(HTTPException) as exc: main._require(None)
    assert exc.value.status_code==401
    monkeypatch.setenv('VISUAL_ENCODER_INTERNAL_KEY','test-secret')
    for value in (None,'Basic test-secret','Bearer wrong'):
        with pytest.raises(HTTPException) as exc: main._require(value)
        assert exc.value.status_code==401
    main._require('Bearer test-secret')

def test_jpeg_only_decode_and_size_controls():
    out=io.BytesIO(); Image.new('RGB',(2,2)).save(out,format='JPEG')
    assert main._decode(__import__('base64').b64encode(out.getvalue()).decode()).size==(2,2)
    with pytest.raises(HTTPException) as exc: main._decode('not base64')
    assert exc.value.status_code==422
    out=io.BytesIO(); Image.new('RGB',(2,2)).save(out,format='PNG')
    with pytest.raises(HTTPException) as exc: main._decode(__import__('base64').b64encode(out.getvalue()).decode())
    assert exc.value.status_code==422
