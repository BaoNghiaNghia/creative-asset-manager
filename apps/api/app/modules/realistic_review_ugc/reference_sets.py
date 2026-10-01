from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.model import (
    RrugcCampaignModel,
    RrugcReferenceAssetModel,
    RrugcReferenceSetItemModel,
    RrugcReferenceSetModel,
)
from app.modules.realistic_review_ugc.repository import RrugcRepository


REFERENCE_SET_MAX_ITEMS = 32
_ROLE_SEPARATOR = re.compile(r"[^a-z0-9]+")


class ReferenceSetError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def normalize_reference_role(value: str) -> str:
    normalized = _ROLE_SEPARATOR.sub("_", str(value or "").strip().lower()).strip("_")
    if not normalized or len(normalized) > 64 or not normalized[0].isalpha():
        raise ReferenceSetError(
            "reference_set_role_invalid",
            "Reference role must start with a letter and contain at most 64 letters, numbers or underscores.",
            status_code=422,
        )
    return normalized


@dataclass(frozen=True, slots=True)
class ReferenceSetBinding:
    reference_set: RrugcReferenceSetModel
    item: RrugcReferenceSetItemModel
    created: bool


class RrugcReferenceSetService:
    def __init__(self, session: Session):
        self.session = session
        self.repository = RrugcRepository(session)

    def create_set(
        self,
        *,
        tenant_id: str,
        user_id: str,
        name: str,
        campaign: RrugcCampaignModel | None = None,
        profile_key: str | None = None,
        description: str | None = None,
    ) -> RrugcReferenceSetModel:
        clean_name = str(name or "").strip()[:200]
        if not clean_name:
            raise ReferenceSetError(
                "reference_set_name_required",
                "Reference set name is required.",
                status_code=422,
            )
        if campaign is not None and campaign.tenant_id != tenant_id:
            raise ReferenceSetError(
                "reference_set_campaign_not_found",
                "Reference set campaign was not found.",
                status_code=404,
            )
        clean_profile = str(profile_key or "").strip()[:100] or None
        clean_description = str(description or "").strip()[:2000] or None
        row = RrugcReferenceSetModel(
            tenant_id=tenant_id,
            name=clean_name,
            campaign_id=campaign.id if campaign is not None else None,
            profile_key=clean_profile,
            description=clean_description,
            status="active",
            created_by_user_id=user_id,
        )
        self.repository.add_reference_set(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def add_item(
        self,
        *,
        tenant_id: str,
        reference_set: RrugcReferenceSetModel,
        reference_asset: RrugcReferenceAssetModel,
        role: str,
        position: int = 0,
        note: str | None = None,
    ) -> ReferenceSetBinding:
        if reference_set.tenant_id != tenant_id or reference_set.status != "active":
            raise ReferenceSetError(
                "reference_set_not_available",
                "Reference set is not available.",
                status_code=409,
            )
        if reference_asset.tenant_id != tenant_id:
            raise ReferenceSetError(
                "reference_asset_not_found",
                "Reference asset was not found.",
                status_code=404,
            )
        if reference_asset.status != "ready":
            raise ReferenceSetError(
                "reference_asset_not_ready",
                "Only ready Reference Library assets can be added to a reference set.",
                status_code=409,
            )
        normalized_role = normalize_reference_role(role)
        clean_position = max(0, min(1000, int(position)))
        clean_note = str(note or "").strip()[:1000] or None

        existing = self.repository.reference_set_item_by_binding(
            tenant_id,
            reference_set.id,
            normalized_role,
            reference_asset.id,
        )
        if existing is not None:
            existing.position = clean_position
            existing.note = clean_note
            self.session.commit()
            self.session.refresh(existing)
            return ReferenceSetBinding(
                reference_set=reference_set,
                item=existing,
                created=False,
            )

        if len(
            self.repository.list_reference_set_items(
                tenant_id,
                reference_set.id,
            )
        ) >= REFERENCE_SET_MAX_ITEMS:
            raise ReferenceSetError(
                "reference_set_item_limit",
                f"Reference set cannot contain more than {REFERENCE_SET_MAX_ITEMS} items.",
                status_code=409,
            )

        row = RrugcReferenceSetItemModel(
            tenant_id=tenant_id,
            reference_set_id=reference_set.id,
            reference_asset_id=reference_asset.id,
            role=normalized_role,
            position=clean_position,
            note=clean_note,
        )
        try:
            self.repository.add_reference_set_item(row)
            self.session.commit()
            self.session.refresh(row)
            return ReferenceSetBinding(
                reference_set=reference_set,
                item=row,
                created=True,
            )
        except IntegrityError:
            self.session.rollback()
            existing = self.repository.reference_set_item_by_binding(
                tenant_id,
                reference_set.id,
                normalized_role,
                reference_asset.id,
            )
            if existing is None:
                raise
            existing.position = clean_position
            existing.note = clean_note
            self.session.commit()
            self.session.refresh(existing)
            return ReferenceSetBinding(
                reference_set=reference_set,
                item=existing,
                created=False,
            )

    def remove_item(
        self,
        *,
        tenant_id: str,
        reference_set: RrugcReferenceSetModel,
        item_id: str,
    ) -> bool:
        if reference_set.tenant_id != tenant_id:
            raise ReferenceSetError(
                "reference_set_not_found",
                "Reference set was not found.",
                status_code=404,
            )
        row = self.repository.get_reference_set_item(
            tenant_id,
            reference_set.id,
            item_id,
        )
        if row is None:
            return False
        self.repository.delete_reference_set_item(row)
        self.session.commit()
        return True
