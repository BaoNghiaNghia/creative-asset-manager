from __future__ import annotations
from datetime import date, datetime, timezone
from decimal import Decimal
from sqlalchemy import func, select
from app.modules.inventory.documents.service import InventoryBusinessFailure, InventoryDocumentValidator
from app.modules.inventory.jobs.model import InventoryJobModel
from app.modules.inventory.jobs.registry import build_inventory_handler_registry
from app.modules.inventory.persistence_model import (InventoryAiAnalysisModel, InventoryDocumentModel, InventoryDocumentPageModel, InventoryItemModel, InventoryLineModel, InventoryLocationModel, InventorySourceFileModel, InventoryTransactionModel, InventoryReviewModel)
from app.modules.inventory.transactions.service import INVENTORY_DOCUMENT_COMMIT_JOB, InventoryDocumentCommitter
from tests.modules.inventory.test_ai_phase5 import InventoryAiPhase5Test

class InventoryPhase7Test(InventoryAiPhase5Test):
 def fixture(self, kind='receipt', status='approved', destination=False):
  suffix=f"{kind}-{status}-{destination}"
  page=self.page(page_id=f"page-{suffix}")
  with self.sessions() as s:
   src=InventoryLocationModel(id=f'loc-src-{suffix}',tenant_id='tenant-a',code=f'SRC-{suffix}',name='Source')
   dst=InventoryLocationModel(id=f'loc-dst-{suffix}',tenant_id='tenant-a',code=f'DST-{suffix}',name='Destination')
   item=InventoryItemModel(id=f'item-{suffix}',tenant_id='tenant-a',sku=f'item-{suffix}',name='Coffee',base_unit='g',conversion_factor=Decimal('100'))
   s.add_all((src,dst,item)); s.flush()
   doc=s.get(InventoryDocumentModel,page.document_id)
   doc.document_type=kind; doc.status=status; doc.business_date=date(2026,8,11); doc.location_id=src.id; doc.destination_location_id=dst.id if destination else None
   analysis=InventoryAiAnalysisModel(id=f'analysis-{suffix}',tenant_id='tenant-a',document_id=doc.id,page_id=page.id,analysis_version=1,idempotency_key=f'analysis-{suffix}',provider='fake',model='fake',prompt_version='p',schema_version='s',status='succeeded',raw_result_json={},extracted_json={})
   s.add(analysis); s.flush()
   line=InventoryLineModel(tenant_id='tenant-a',document_id=doc.id,page_id=page.id,analysis_id=analysis.id,line_number=1,raw_item_name='Coffee',item_id=item.id,raw_values_json={'raw_item_name':'Coffee'},normalized_values_json={},whole_quantity=Decimal('2'),fraction_quantity=Decimal('5'),whole_unit='pack',fraction_unit='g',conversion_factor_snapshot=Decimal('100'),quantity_base_unit=Decimal('205'),waste_quantity=Decimal('7') if kind=='waste' else None,waste_reason='damaged' if kind=='waste' else None,confidence=Decimal('0.99'),validation_status='valid')
   s.add(line); s.commit(); return doc.id,line.id
 def commit(self, doc):
  return InventoryDocumentCommitter(self.sessions).execute(InventoryJobModel(tenant_id='tenant-a',job_type=INVENTORY_DOCUMENT_COMMIT_JOB,entity_type='inventory_document',entity_id=doc,idempotency_key='commit-'+doc,payload_json={'document_id':doc}))
 def test_approved_receipt_is_idempotent_and_preserves_provenance(self):
  doc,line=self.fixture(); self.commit(doc); self.commit(doc)
  with self.sessions() as s:
   rows=list(s.scalars(select(InventoryTransactionModel))); self.assertEqual(len(rows),1); row=rows[0]; self.assertEqual((row.transaction_type,row.quantity_base_unit,row.business_date,row.source_document_id,row.source_line_id),('receipt',Decimal('205'),date(2026,8,11),doc,line)); self.assertEqual(row.conversion_factor_snapshot,Decimal('100'))
 def test_non_approved_documents_create_zero_rows(self):
  for status in ('needs_review','needs_reupload','rejected','validating'):
   doc,_=self.fixture(status=status)
   with self.assertRaises(InventoryBusinessFailure): self.commit(doc)
  with self.sessions() as s:self.assertEqual(s.scalar(select(func.count(InventoryTransactionModel.id))),0)
 def test_opening_closing_and_waste_semantics(self):
  for kind,expected in (('opening','opening_balance'),('stock_count','closing_count'),('waste','waste')):
   doc,_=self.fixture(kind=kind); self.commit(doc)
  with self.sessions() as s:
   rows={r.transaction_type:r for r in s.scalars(select(InventoryTransactionModel))}; self.assertEqual(set(rows),{'opening_balance','closing_count','waste'}); self.assertEqual(rows['waste'].quantity_base_unit,Decimal('7')); self.assertEqual(rows['waste'].metadata_json['waste_reason'],'damaged')
 def test_transfer_creates_linked_atomic_legs(self):
  doc,line=self.fixture(kind='warehouse_transfer',destination=True); self.commit(doc)
  with self.sessions() as s:
   rows=list(s.scalars(select(InventoryTransactionModel).where(InventoryTransactionModel.source_document_id==doc))); self.assertEqual({r.transaction_type for r in rows},{'transfer_out','transfer_in'}); self.assertEqual({r.source_line_id for r in rows},{line}); self.assertEqual({r.business_date for r in rows},{date(2026,8,11)}); self.assertEqual({r.location_id for r in rows},{f'loc-src-warehouse_transfer-approved-True',f'loc-dst-warehouse_transfer-approved-True'})
 def test_registry_is_exact_phase_seven_set(self):
  self.assertEqual(build_inventory_handler_registry().job_types,('inventory_file_download','inventory_document_prepare','inventory_document_analyze','inventory_document_normalize','inventory_document_validate','inventory_document_commit'))
 def test_auto_approval_enqueues_one_commit_job_without_ledger_mutation(self):
  doc,_=self.fixture(status='validating')
  validate_job=InventoryJobModel(tenant_id='tenant-a',job_type='inventory_document_validate',entity_type='inventory_document',entity_id=doc,idempotency_key='validate-'+doc,payload_json={'document_id':doc})
  validator=InventoryDocumentValidator(self.sessions)
  validator.execute(validate_job); validator.execute(validate_job)
  with self.sessions() as s:
   jobs=list(s.scalars(select(InventoryJobModel).where(InventoryJobModel.job_type==INVENTORY_DOCUMENT_COMMIT_JOB, InventoryJobModel.entity_id==doc)))
   self.assertEqual(len(jobs),1)
   self.assertEqual(s.scalar(select(func.count(InventoryTransactionModel.id))),0)

 def test_human_approval_enqueues_one_commit_job_without_ledger_mutation(self):
  doc,line=self.fixture(status='needs_review')
  from app.modules.inventory.review.service import InventoryReviewService
  with self.sessions() as s:
   review=InventoryReviewModel(tenant_id='tenant-a',document_id=doc,line_id=line,idempotency_key='review-'+doc,reason_code='manual',original_value_json={'raw':'Coffee'},suggested_value_json={})
   s.add(review); s.commit(); review_id=review.id
  service=InventoryReviewService(self.sessions)
  service.mutate('tenant-a',review_id,'approve','reviewer-a')
  service.mutate('tenant-a',review_id,'approve','reviewer-a')
  with self.sessions() as s:
   jobs=list(s.scalars(select(InventoryJobModel).where(InventoryJobModel.job_type==INVENTORY_DOCUMENT_COMMIT_JOB, InventoryJobModel.entity_id==doc)))
   self.assertEqual(len(jobs),1)
   self.assertEqual(s.scalar(select(func.count(InventoryTransactionModel.id))),0)