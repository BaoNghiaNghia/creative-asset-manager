from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.providers.contracts import AssetStorageProvider, StorageProviderError, StoreAssetInput
from app.modules.realistic_review_ugc.model import RrugcProductModel, RrugcProductReferenceModel
from app.modules.realistic_review_ugc.repository import RrugcRepository
from app.modules.realistic_review_ugc.schema import ProductCreateRequest, ProductUpdateRequest
from app.modules.realistic_review_ugc.service import RrugcError
from app.modules.visual_search.preprocess import (
    VisualImagePreparationError,
    VisualPreprocessLimits,
    decode_visual_image,
)


PRODUCT_REFERENCE_MAX_BYTES = 20_000_000
PRODUCT_REFERENCE_VIEWS = frozenset({
    "front",
    "front_45_left",
    "front_45_right",
    "side_left",
    "side_right",
    "back",
    "top",
    "logo_closeup",
    "embroidery_closeup",
    "material_closeup",
})


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _image_content_type(image_format: str) -> tuple[str, str]:
    mapping = {
        "JPEG": ("image/jpeg", ".jpg"),
        "MPO": ("image/jpeg", ".jpg"),
        "PNG": ("image/png", ".png"),
        "WEBP": ("image/webp", ".webp"),
        "BMP": ("image/bmp", ".bmp"),
        "GIF": ("image/gif", ".gif"),
        "TIFF": ("image/tiff", ".tif"),
        "AVIF": ("image/avif", ".avif"),
        "HEIF": ("image/heif", ".heif"),
        "HEIC": ("image/heic", ".heic"),
    }
    result = mapping.get(image_format.upper())
    if result is None:
        raise RrugcError(
            "product_reference_format_unsupported",
            "Product reference image format is unsupported.",
            status_code=422,
        )
    return result


async def _bytes_body(content: bytes, chunk_size: int = 64 * 1024) -> AsyncIterator[bytes]:
    for offset in range(0, len(content), chunk_size):
        yield content[offset : offset + chunk_size]


class RrugcProductRegistry:
    def __init__(self, session: Session):
        self.session = session
        self.repository = RrugcRepository(session)

    def create_product(
        self,
        *,
        tenant_id: str,
        user_id: str,
        request: ProductCreateRequest,
    ) -> RrugcProductModel:
        sku = request.sku.strip().upper()
        if self.repository.product_by_sku(tenant_id, sku) is not None:
            raise RrugcError(
                "product_sku_exists",
                "A product with this SKU already exists.",
                status_code=409,
            )
        row = RrugcProductModel(
            tenant_id=tenant_id,
            sku=sku,
            name=request.name.strip(),
            product_type=request.product_type.strip().lower(),
            color=_clean_optional(request.color),
            material=_clean_optional(request.material),
            crown_profile=_clean_optional(request.crown_profile),
            crown_height_mm=request.crown_height_mm,
            brim_style=_clean_optional(request.brim_style),
            brim_length_mm=request.brim_length_mm,
            circumference_mm=request.circumference_mm,
            logo_position=_clean_optional(request.logo_position),
            fit_notes=_clean_optional(request.fit_notes),
            created_by_user_id=user_id,
            status="active",
            revision=1,
        )
        try:
            self.session.add(row)
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise RrugcError(
                "product_sku_exists",
                "A product with this SKU already exists.",
                status_code=409,
            ) from exc
        self.session.refresh(row)
        return row

    def update_product(
        self,
        *,
        product: RrugcProductModel,
        request: ProductUpdateRequest,
    ) -> RrugcProductModel:
        if product.status != "active":
            raise RrugcError(
                "product_archived",
                "Archived products cannot be edited.",
                status_code=409,
            )
        values = request.model_dump(exclude_unset=True)
        for field, value in values.items():
            if field in {"name", "product_type"} and value is None:
                raise RrugcError(
                    "product_field_invalid",
                    f"{field} cannot be empty.",
                    status_code=422,
                )
            if isinstance(value, str):
                value = _clean_optional(value)
                if field in {"name", "product_type"} and not value:
                    raise RrugcError(
                        "product_field_invalid",
                        f"{field} cannot be empty.",
                        status_code=422,
                    )
                if field == "product_type" and value:
                    value = value.lower()
            setattr(product, field, value)
        product.revision += 1
        self.session.commit()
        self.session.refresh(product)
        return product

    def archive_product(self, product: RrugcProductModel) -> RrugcProductModel:
        if product.status != "archived":
            product.status = "archived"
            product.archived_at = datetime.now(timezone.utc)
            product.revision += 1
            self.session.commit()
            self.session.refresh(product)
        return product

    def archive_reference(
        self, reference: RrugcProductReferenceModel
    ) -> RrugcProductReferenceModel:
        if reference.status != "archived":
            reference.status = "archived"
            reference.archived_at = datetime.now(timezone.utc)
            self.session.commit()
            self.session.refresh(reference)
        return reference

    async def upload_reference(
        self,
        *,
        tenant_id: str,
        user_id: str,
        product_id: str,
        view_type: str,
        original_filename: str | None,
        content: bytes,
        storage: AssetStorageProvider,
    ) -> RrugcProductReferenceModel:
        if view_type not in PRODUCT_REFERENCE_VIEWS:
            raise RrugcError(
                "product_reference_view_invalid",
                "Product reference view is invalid.",
                status_code=422,
            )
        if not content:
            raise RrugcError(
                "product_reference_empty",
                "Product reference image is empty.",
                status_code=422,
            )
        if len(content) > PRODUCT_REFERENCE_MAX_BYTES:
            raise RrugcError(
                "product_reference_too_large",
                "Product reference image exceeds the 20 MB limit.",
                status_code=413,
            )

        try:
            prepared = decode_visual_image(
                content,
                limits=VisualPreprocessLimits(
                    max_source_bytes=PRODUCT_REFERENCE_MAX_BYTES,
                    max_source_width=20_000,
                    max_source_height=20_000,
                    max_decode_pixels=100_000_000,
                ),
            )
        except VisualImagePreparationError as exc:
            raise RrugcError(
                "product_reference_invalid_image",
                str(exc),
                status_code=422,
            ) from exc
        try:
            image_format = prepared.source_format
            width = prepared.width
            height = prepared.height
        finally:
            prepared.image.close()

        content_hash = hashlib.sha256(content).hexdigest()
        content_type, suffix = _image_content_type(image_format)

        product = self.repository.lock_product(tenant_id, product_id)
        if product is None:
            raise RrugcError("product_not_found", "Product not found.", status_code=404)
        if product.status != "active":
            raise RrugcError(
                "product_archived",
                "Archived products cannot receive new references.",
                status_code=409,
            )

        duplicate = self.repository.product_reference_by_hash(
            tenant_id, product_id, view_type, content_hash
        )
        if duplicate is not None:
            return duplicate

        version = self.repository.latest_reference_version(
            tenant_id, product_id, view_type
        ) + 1
        reference_id = str(uuid4())
        reusable = self.repository.reusable_product_reference_by_hash(
            tenant_id, content_hash
        )

        if reusable is not None:
            stored_remote_file_id = reusable.remote_file_id
            stored_remote_folder_id = reusable.remote_folder_id
            stored_web_url = reusable.web_url
            reused_storage = True
        else:
            try:
                stored = await storage.store_asset(
                    StoreAssetInput(
                        tenant_id=tenant_id,
                        content_hash=content_hash,
                        body=_bytes_body(content),
                        asset_id=reference_id,
                        content_type=content_type,
                        size_bytes=len(content),
                        filename=(
                            f"PRODUCT_{product.sku}_{view_type}_v{version}_{reference_id}{suffix}"
                        ),
                    )
                )
            except StorageProviderError as exc:
                raise RrugcError(
                    "product_reference_storage_failed",
                    "Product reference could not be saved to Managed Drive.",
                    status_code=503,
                    retryable=bool(exc.retryable),
                ) from exc
            stored_remote_file_id = stored.remote_file_id
            stored_remote_folder_id = stored.remote_folder_id
            stored_web_url = stored.web_url
            reused_storage = False

        row = RrugcProductReferenceModel(
            id=reference_id,
            tenant_id=tenant_id,
            product_id=product_id,
            view_type=view_type,
            version=version,
            status="active",
            content_hash=content_hash,
            original_filename=(original_filename or "").strip()[:255] or None,
            content_type=content_type,
            size_bytes=len(content),
            width=width,
            height=height,
            image_format=image_format,
            remote_file_id=stored_remote_file_id,
            remote_folder_id=stored_remote_folder_id,
            web_url=stored_web_url,
            reused_storage=reused_storage,
            created_by_user_id=user_id,
        )
        try:
            self.session.add(row)
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            # A concurrent upload may have created the same view/version. Return
            # the exact content match when possible instead of duplicating state.
            duplicate = self.repository.product_reference_by_hash(
                tenant_id, product_id, view_type, content_hash
            )
            if duplicate is not None:
                return duplicate
            raise RrugcError(
                "product_reference_version_conflict",
                "Reference version changed during upload. Retry the upload.",
                status_code=409,
                retryable=True,
            ) from exc
        self.session.refresh(row)
        return row
