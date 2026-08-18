import hashlib, json
from datetime import timezone

def _value(value):
    if hasattr(value, "astimezone"): return value.astimezone(timezone.utc).isoformat()
    return value.strip() if isinstance(value, str) else value

def _hash(value): return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()

def build_video_source_fingerprint(source_asset):
    return _hash({key: _value(getattr(source_asset, key, None)) for key in ("external_source_id", "external_asset_id", "provider_checksum", "provider_version", "source_modified_at", "size_bytes", "mime_type")})

def build_video_analysis_idempotency_key(**values):
    keys=("tenant_id","source_asset_id","source_fingerprint","video_metadata_profile_id","metadata_profile_version","prompt_version","analysis_version","ai_provider","ai_model")
    return _hash({key: _value(values.get(key)) for key in keys})
