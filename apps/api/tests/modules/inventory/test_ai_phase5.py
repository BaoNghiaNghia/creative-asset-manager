from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.modules.assets.model import ExternalSourceModel
from app.modules.ai_metadata.model import AssetAiAnalysisModel
from app.modules.auth_persistence.model import TenantModel
from app.modules.inventory.ai.gateway import InventoryAiGatewayError, InventoryAiGatewayResult
from app.modules.inventory.ai.service import INVENTORY_DOCUMENT_ANALYZE_JOB, InventoryAnalyzeFailure, InventoryDocumentAnalyzer
from app.modules.inventory.jobs.model import InventoryJobModel
from app.modules.inventory.jobs.registry import build_inventory_handler_registry
from app.modules.inventory.model import InventoryAiControlModel
from app.modules.inventory.persistence_model import InventoryAiAnalysisModel, InventoryDocumentModel, InventoryDocumentPageModel, InventorySourceFileModel
from app.modules.inventory.preparation.storage import InventoryPreparedStorage


class FakeGateway:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, 0
    def analyze(self, **_kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


class InventoryAiPhase5Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.engine = create_engine(f"sqlite:///{self.root / 'db.sqlite'}")
        event.listen(self.engine, "connect", lambda c, _: c.execute("PRAGMA foreign_keys=ON"))
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

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def page(self, *, tenant="tenant-a", page_id="page-a", prepared=True):
        content = b"prepared-jpeg"
        digest = hashlib.sha256(content).hexdigest()
        key = f"inventory/{tenant}/prepared/file/v1/{digest}.jpg"
        target = self.root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        with self.sessions() as session:
            source = InventorySourceFileModel(id=f"file-{page_id}", tenant_id=tenant, external_source_id="source-a" if tenant == "tenant-a" else "source-b", drive_file_id=f"drive-{page_id}", filename="sheet.jpg", mime_type="image/jpeg", drive_modified_time=datetime.now(timezone.utc), status="downloaded", content_sha256=digest)
            document = InventoryDocumentModel(id=f"doc-{page_id}", tenant_id=tenant, idempotency_key=f"doc-{page_id}", document_type="unclassified", status="prepared", expected_pages=1, received_pages=1)
            page = InventoryDocumentPageModel(id=page_id, tenant_id=tenant, document_id=document.id, source_file_id=source.id, drive_file_id=source.drive_file_id, page_number=1, page_count=1, content_sha256=digest, preparation_status="prepared" if prepared else "terminal_failure", prepared_storage_key=key if prepared else None, prepared_content_sha256=digest, prepared_mime_type="image/jpeg")
            session.add_all((source, document))
            session.flush()
            session.add(page)
            session.commit()
        return page

    def control(self, **values):
        payload = {"tenant_id": "tenant-a", "enabled": True, "provider": "fake", "allowed_models_json": ["fake-v1"], "max_concurrent": 1, "min_start_interval_seconds": 0, "per_run_limit": 1, "daily_budget_micros": 0, "monthly_budget_micros": 0}
        payload.update(values)
        with self.sessions() as session:
            session.add(InventoryAiControlModel(**payload))
            session.commit()

    @staticmethod
    def result(cost=7):
        extracted = {"document_type": "stock_count", "business_date": None, "location": None, "page_number": 1, "page_count": 1, "raw_item_lines": [{"raw_item_name": "coffee", "whole_quantity": 2, "whole_unit": "bag", "confidence": 0.9}]}
        return InventoryAiGatewayResult(raw_response_json={"candidate": extracted}, extracted_json=extracted, provider_request_id="safe-request-id", usage_json={"input_tokens": 12}, estimated_cost_micros=cost)

    def job(self, page):
        return InventoryJobModel(tenant_id=page.tenant_id, job_type=INVENTORY_DOCUMENT_ANALYZE_JOB, entity_type="inventory_document_page", entity_id=page.id, idempotency_key=f"job-{page.id}", payload_json={"page_id": page.id})

    def analyzer(self, gateway, enabled=True):
        return InventoryDocumentAnalyzer(self.sessions, prepared_storage=InventoryPreparedStorage(self.root), gateway=gateway, enabled=enabled)

    def test_success_persists_profile_versions_raw_extracted_usage_and_cost(self):
        page = self.page(); self.control(); gateway = FakeGateway(self.result())
        self.analyzer(gateway).execute(self.job(page))
        with self.sessions() as session:
            row = session.scalar(select(InventoryAiAnalysisModel))
            self.assertEqual(row.status, "succeeded")
            self.assertEqual(row.extraction_profile, "inventory-stock-sheet")
            self.assertEqual(row.prompt_version, "inventory-stock-sheet-prompt-v1")
            self.assertEqual(row.schema_version, "inventory-stock-sheet-schema-v1")
            self.assertEqual(row.raw_result_json["candidate"]["document_type"], "stock_count")
            self.assertEqual(row.extracted_json["document_type"], "stock_count")
            self.assertEqual(row.usage_json["input_tokens"], 12)
            self.assertEqual(row.estimated_cost_micros, 7)
        self.assertEqual(gateway.calls, 1)

    def test_unprepared_cross_tenant_disabled_and_stop_never_call_provider(self):
        gateway = FakeGateway(self.result())
        blocked = self.page(prepared=False); self.control()
        with self.assertRaises(InventoryAnalyzeFailure): self.analyzer(gateway).execute(self.job(blocked))
        other = self.page(tenant="tenant-b", page_id="page-b")
        with self.assertRaises(InventoryAnalyzeFailure): self.analyzer(gateway).execute(InventoryJobModel(tenant_id="tenant-a", job_type=INVENTORY_DOCUMENT_ANALYZE_JOB, entity_type="inventory_document_page", entity_id=other.id, idempotency_key="other", payload_json={"page_id": other.id}))
        self.assertEqual(gateway.calls, 0)
        with self.assertRaises(InventoryAnalyzeFailure): self.analyzer(gateway, enabled=False).execute(self.job(blocked))
        self.assertEqual(gateway.calls, 0)

    def test_timeout_is_retryable_invalid_output_terminal_and_success_idempotent(self):
        page = self.page(); self.control(); timeout = FakeGateway(error=InventoryAiGatewayError("inventory_ai_provider_timeout", retryable=True))
        with self.assertRaises(InventoryAnalyzeFailure) as raised: self.analyzer(timeout).execute(self.job(page))
        self.assertTrue(raised.exception.retryable)
        with self.sessions() as session: self.assertEqual(session.scalar(select(InventoryAiAnalysisModel)).status, "retryable_failure")
        with self.sessions() as session: session.query(InventoryAiControlModel).update({"min_start_interval_seconds": 0}); session.commit()
        good = FakeGateway(self.result()); self.analyzer(good).execute(self.job(page)); self.analyzer(good).execute(self.job(page))
        with self.sessions() as session: self.assertEqual(session.scalar(select(func.count(InventoryAiAnalysisModel.id))), 1)
        self.assertEqual(good.calls, 1)


    def test_daily_budget_and_emergency_stop_block_before_provider_call(self):
        page = self.page(); self.control(daily_budget_micros=5)
        gateway = FakeGateway(self.result(cost=7))
        with self.assertRaises(InventoryAnalyzeFailure) as raised:
            InventoryDocumentAnalyzer(self.sessions, prepared_storage=InventoryPreparedStorage(self.root), gateway=gateway, enabled=True, estimated_cost_micros=7).execute(self.job(page))
        self.assertEqual(raised.exception.code, "inventory_ai_daily_budget_exceeded")
        self.assertEqual(gateway.calls, 0)

    def test_inventory_analysis_never_creates_creative_ai_rows(self):
        page = self.page(); self.control(); gateway = FakeGateway(self.result())
        self.analyzer(gateway).execute(self.job(page))
        with self.sessions() as session:
            self.assertEqual(session.scalar(select(func.count(AssetAiAnalysisModel.id))), 0)

    def test_registry_includes_only_completed_phase_five_handlers(self):
        self.assertEqual(build_inventory_handler_registry().job_types, ("inventory_file_download", "inventory_document_prepare", "inventory_document_analyze", "inventory_document_normalize", "inventory_document_validate", "inventory_document_commit"))