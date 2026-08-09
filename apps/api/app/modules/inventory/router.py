from fastapi import APIRouter


# Phase 1 establishes an isolated routing boundary without exposing business APIs.
router = APIRouter(prefix="/api/inventory", tags=["inventory"])
