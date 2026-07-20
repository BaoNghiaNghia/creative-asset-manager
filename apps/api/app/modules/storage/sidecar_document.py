from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.ai_metadata.model import AssetAiAnalysisModel
from app.modules.assets.model import (
    AssetModel,
    AssetSourceLinkModel,
    ExternalSourceModel,
    SourceAssetModel,
)

_SECRET_KEY = re.compile(
    r"(?:password|secret|token|credential|authorization|api[_-]?key|access[_-]?key|oauth)",
    re.IGNORECASE,
)
_SIGNED_QUERY_KEYS = {
    "signature",
    "sig",
    "token",
    "access_token",
    "key",
    "x-goog-signature",
    "x-amz-signature",
    "x-amz-credential",
}


def _safe_metadata(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _safe_metadata(item)
            for key, item in value.items()
            if not _SECRET_KEY.search(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [_safe_metadata(item) for item in value]
    if isinstance(value, str):
        parsed = urlsplit(value)
        if parsed.scheme in {"http", "https"}:
            query_keys = {key.casefold() for key, _ in parse_qsl(parsed.query)}
            if parsed.username or parsed.password or query_keys & _SIGNED_QUERY_KEYS:
                return "[redacted-url]"
    return copy.deepcopy(value)


class MetadataSidecarDocumentBuilder:
    schema_version = "cam-metadata-sidecar-v1"

    def __init__(self, session: Session):
        self.session = session

    def build(self, tenant_id: str, analysis_id: str) -> tuple[dict[str, Any], str]:
        analysis = self.session.get(AssetAiAnalysisModel, analysis_id)
        if (
            analysis is None
            or analysis.tenant_id != tenant_id
            or analysis.status != "completed"
            or analysis.metadata_json is None
        ):
            raise LookupError(analysis_id)
        asset = self.session.get(AssetModel, analysis.asset_id)
        if asset is None or asset.tenant_id != tenant_id:
            raise LookupError(analysis.asset_id)

        rows = self.session.execute(
            select(SourceAssetModel, ExternalSourceModel)
            .join(
                AssetSourceLinkModel,
                AssetSourceLinkModel.source_asset_id == SourceAssetModel.id,
            )
            .join(
                ExternalSourceModel,
                ExternalSourceModel.id == SourceAssetModel.external_source_id,
            )
            .where(
                AssetSourceLinkModel.tenant_id == tenant_id,
                AssetSourceLinkModel.asset_id == asset.id,
                SourceAssetModel.tenant_id == tenant_id,
                ExternalSourceModel.tenant_id == tenant_id,
            )
            .order_by(
                ExternalSourceModel.source_type,
                ExternalSourceModel.id,
                SourceAssetModel.external_asset_id,
            )
        )
        sources = [
            {
                "source_type": source.source_type,
                "source_id": source.id,
                "external_asset_id": source_asset.external_asset_id,
                "filename": source_asset.filename,
                "provider_checksum": source_asset.provider_checksum,
                "provider_version": source_asset.provider_version,
            }
            for source_asset, source in rows
        ]
        document = {
            "schema_version": self.schema_version,
            "authoritative_source": "postgresql",
            "asset": {
                "asset_id": asset.id,
                "content_hash": asset.content_hash,
            },
            "source_references": sources,
            "analysis": {
                "analysis_id": analysis.id,
                "metadata_profile": analysis.metadata_profile,
                "metadata_profile_version": analysis.metadata_profile_version,
                "prompt_version": analysis.prompt_version,
                "pipeline_version": analysis.pipeline_version,
                "ai_provider": analysis.ai_provider,
                "ai_model": analysis.ai_model,
                "completed_at": (
                    analysis.completed_at.isoformat() if analysis.completed_at else None
                ),
            },
            "metadata_json": _safe_metadata(analysis.metadata_json),
            "search_projection_version": analysis.search_projection_version,
        }
        encoded = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return document, hashlib.sha256(encoded).hexdigest()
