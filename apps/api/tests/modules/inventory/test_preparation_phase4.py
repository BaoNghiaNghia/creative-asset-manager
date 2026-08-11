from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.modules.assets.model import ExternalSourceModel
from app.modules.auth_persistence.model import TenantModel
from app.modules.inventory.jobs.model import InventoryJobModel
from app.modules.inventory.jobs.registry import build_inventory_handler_registry
from app.modules.inventory.persistence_model import (
    InventoryDocumentModel,
    InventoryDocumentPageModel,
    InventorySourceFileModel,
)
from app.modules.inventory.preparation.image import (
    InventoryImagePreparationLimits,
    StatelessInventoryImagePreparer,
)
from app.modules.inventory.preparation.service import (
    INVENTORY_DOCUMENT_PREPARE_JOB,
    InventoryDocumentPreparer,
    InventoryPrepareFailure,
)
from app.modules.inventory.preparation.storage import InventoryPreparedStorage


class InventoryPreparationPhase4Test(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name) / "storage"
        self.engine = create_engine(f"sqlite:///{Path(self.directory.name) / 'phase4.db'}")
        event.listen(self.engine, "connect", lambda connection, _record: connection.execute("PRAGMA foreign_keys=ON"))
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        with self.sessions() as session:
            session.add_all((
                TenantModel(id="tenant-a", name="A", slug="a"),
                TenantModel(id="tenant-b", name="B", slug="b"),
                ExternalSourceModel(id="source-a", tenant_id="tenant-a", source_key="a", source_type="google_drive"),
                ExternalSourceModel(id="source-b", tenant_id="tenant-b", source_key="b", source_type="google_drive"),
            ))
            session.commit()
        self.limits = InventoryImagePreparationLimits(
            max_source_bytes=1_000_000,
            max_source_width=1000,
            max_source_height=1000,
            max_decode_pixels=1_000_000,
            max_output_bytes=1_000_000,
            max_width=64,
            max_height=64,
            jpeg_quality=85,
        )
        self.preparer = InventoryDocumentPreparer(
            self.sessions,
            source_storage=InventoryPreparedStorage(self.root),
            prepared_storage=InventoryPreparedStorage(self.root),
            image_preparer=StatelessInventoryImagePreparer(self.limits),
        )

    def tearDown(self) -> None:
        self.engine.dispose()
        self.directory.cleanup()

    @staticmethod
    def _jpeg(width: int = 160, height: int = 80) -> bytes:
        import io
        payload = io.BytesIO()
        Image.new("RGB", (width, height), "navy").save(payload, format="JPEG")
        return payload.getvalue()

    def _source(self, *, source_id: str = "file-a", content: bytes | None = None, status: str = "downloaded", tenant: str = "tenant-a", source: str = "source-a") -> InventorySourceFileModel:
        content = self._jpeg() if content is None else content
        digest = hashlib.sha256(content).hexdigest()
        key = f"inventory/{tenant}/source/{source_id}/{digest}.jpg"
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        with self.sessions() as session:
            row = InventorySourceFileModel(
                id=source_id,
                tenant_id=tenant,
                external_source_id=source,
                drive_file_id=f"drive-{source_id}",
                filename=f"{source_id}.jpg",
                mime_type="image/jpeg",
                drive_modified_time=datetime(2030, 1, 1, tzinfo=timezone.utc),
                drive_size=len(content),
                content_sha256=digest,
                storage_key=key,
                status=status,
            )
            session.add(row)
            session.commit()
            return row

    @staticmethod
    def _job(source: InventorySourceFileModel) -> InventoryJobModel:
        return InventoryJobModel(
            tenant_id=source.tenant_id,
            job_type=INVENTORY_DOCUMENT_PREPARE_JOB,
            entity_type="inventory_source_file",
            entity_id=source.id,
            idempotency_key=f"inventory-document-prepare:v1:{source.id}",
            payload_json={"source_file_id": source.id},
        )

    def test_downloaded_source_prepares_document_page_and_inventory_artifact(self) -> None:
        source = self._source()
        self.preparer.execute(self._job(source))
        with self.sessions() as session:
            current = session.get(InventorySourceFileModel, source.id)
            document = session.scalar(select(InventoryDocumentModel))
            page = session.scalar(select(InventoryDocumentPageModel))
            self.assertEqual(current.preparation_status, "prepared")
            self.assertEqual(document.status, "prepared")
            self.assertEqual(document.document_type, "unclassified")
            self.assertEqual(document.received_pages, 1)
            self.assertEqual(page.preparation_status, "prepared")
            self.assertEqual(page.prepared_mime_type, "image/jpeg")
            self.assertEqual((page.image_width, page.image_height), (64, 32))
            self.assertEqual(page.prepared_size_bytes, (self.root / page.prepared_storage_key).stat().st_size)
            self.assertTrue(page.prepared_storage_key.startswith("inventory/tenant-a/prepared/file-a/v1/"))
            self.assertEqual(page.prepared_content_sha256, hashlib.sha256((self.root / page.prepared_storage_key).read_bytes()).hexdigest())

    @staticmethod
    def _encoded(image_format: str, width: int = 40, height: int = 20, *, orientation: int | None = None) -> bytes:
        import io
        payload = io.BytesIO()
        image = Image.new("RGB", (width, height), "orange")
        options = {}
        if orientation is not None:
            exif = Image.Exif()
            exif[274] = orientation
            options["exif"] = exif
        image.save(payload, format=image_format, **options)
        return payload.getvalue()

    def test_jpeg_png_webp_and_avif_prepare_to_jpeg(self) -> None:
        for image_format in ("JPEG", "PNG", "WEBP", "AVIF"):
            with self.subTest(image_format=image_format):
                source = self._source(source_id=f"format-{image_format.lower()}", content=self._encoded(image_format))
                self.preparer.execute(self._job(source))
                with self.sessions() as session:
                    page = session.scalar(select(InventoryDocumentPageModel).where(InventoryDocumentPageModel.source_file_id == source.id))
                    self.assertEqual(page.preparation_status, "prepared")
                    self.assertEqual(page.prepared_mime_type, "image/jpeg")

    def test_orientation_is_normalized_before_dimensions_are_persisted(self) -> None:
        source = self._source(source_id="rotated", content=self._encoded("JPEG", 20, 40, orientation=6))
        self.preparer.execute(self._job(source))
        with self.sessions() as session:
            page = session.scalar(select(InventoryDocumentPageModel).where(InventoryDocumentPageModel.source_file_id == source.id))
            self.assertEqual((page.image_width, page.image_height), (40, 20))

    def test_only_downloaded_or_duplicate_sources_are_eligible(self) -> None:
        for index, status in enumerate(("unsupported", "retryable_failure", "terminal_failure", "queued", "downloading")):
            source = self._source(source_id=f"blocked-{index}", status=status)
            with self.assertRaises(InventoryPrepareFailure) as raised:
                self.preparer.execute(self._job(source))
            self.assertEqual(raised.exception.code, "inventory_prepare_source_not_downloaded")
        with self.sessions() as session:
            self.assertEqual(session.scalar(select(func.count(InventoryDocumentModel.id))), 0)
            self.assertEqual(session.scalar(select(func.count(InventoryDocumentPageModel.id))), 0)

    def test_missing_source_storage_is_retryable_and_creates_no_page(self) -> None:
        source = self._source(source_id="missing")
        (self.root / source.storage_key).unlink()
        with self.assertRaises(InventoryPrepareFailure) as raised:
            self.preparer.execute(self._job(source))
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.code, "inventory_prepare_source_storage_missing")
        with self.sessions() as session:
            current = session.get(InventorySourceFileModel, source.id)
            self.assertEqual(current.preparation_status, "retryable_failure")
            self.assertEqual(session.scalar(select(func.count(InventoryDocumentPageModel.id))), 0)

    def test_corrupt_image_is_terminal_and_never_exposed_as_page(self) -> None:
        source = self._source(source_id="corrupt", content=b"not-an-image")
        with self.assertRaises(InventoryPrepareFailure) as raised:
            self.preparer.execute(self._job(source))
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(raised.exception.code, "inventory_prepare_invalid_image")
        with self.sessions() as session:
            self.assertEqual(session.get(InventorySourceFileModel, source.id).preparation_status, "terminal_failure")
            self.assertEqual(session.scalar(select(func.count(InventoryDocumentPageModel.id))), 0)

    def test_repeated_execution_is_idempotent_and_registry_includes_phase_five(self) -> None:
        source = self._source(source_id="repeat")
        self.preparer.execute(self._job(source))
        self.preparer.execute(self._job(source))
        with self.sessions() as session:
            self.assertEqual(session.scalar(select(func.count(InventoryDocumentModel.id))), 1)
            self.assertEqual(session.scalar(select(func.count(InventoryDocumentPageModel.id))), 1)
        self.assertEqual(
            build_inventory_handler_registry().job_types,
            ("inventory_file_download", "inventory_document_prepare", "inventory_document_analyze", "inventory_document_normalize", "inventory_document_validate"),
        )
