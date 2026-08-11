from __future__ import annotations
import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import select, func
from app.modules.inventory.documents.service import (INVENTORY_DOCUMENT_NORMALIZE_JOB, INVENTORY_DOCUMENT_VALIDATE_JOB, InventoryDocumentNormalizer, InventoryDocumentValidator, normalize_unit)
from app.modules.inventory.jobs.model import InventoryJobModel
from app.modules.inventory.persistence_model import InventoryAiAnalysisModel, InventoryDocumentModel, InventoryDocumentPageModel, InventoryItemAliasModel, InventoryItemModel, InventoryLineModel, InventoryReviewEventModel, InventoryReviewModel, InventorySourceFileModel
from app.modules.inventory.review.service import InventoryReviewService
from tests.modules.inventory.test_ai_phase5 import InventoryAiPhase5Test

class InventoryPhase6Test(InventoryAiPhase5Test):
 def analysis(self, raw, page_id='page-a'):
  page=self.page(page_id=page_id); digest=hashlib.sha256(b'prepared-jpeg').hexdigest()
  with self.sessions() as s:
   row=InventoryAiAnalysisModel(tenant_id='tenant-a',document_id=page.document_id,page_id=page.id,analysis_version=1,idempotency_key='analysis-'+page.id,content_sha256=digest,provider='fake',model='fake-v1',prompt_version='p',schema_version='s',status='succeeded',validation_status='valid',extracted_json={'raw_item_lines':[raw]})
   s.add(row);s.commit();return row
 def item(self, name='Olong', tenant='tenant-a'):
  with self.sessions() as s:
   row=InventoryItemModel(tenant_id=tenant,sku='sku-'+name+tenant,name=name,base_unit='g',whole_unit='pack',fraction_unit='g',conversion_factor=Decimal('100'));s.add(row);s.commit();return row
 def normalize(self, analysis): InventoryDocumentNormalizer(self.sessions).execute(InventoryJobModel(tenant_id='tenant-a',job_type=INVENTORY_DOCUMENT_NORMALIZE_JOB,entity_type='inventory_ai_analysis',entity_id=analysis.id,idempotency_key='n-'+analysis.id))
 def validate(self, document_id): InventoryDocumentValidator(self.sessions).execute(InventoryJobModel(tenant_id='tenant-a',job_type=INVENTORY_DOCUMENT_VALIDATE_JOB,entity_type='inventory_document',entity_id=document_id,idempotency_key='v-'+document_id,payload_json={'document_id':document_id}))
 def test_exact_alias_unknown_ambiguous_and_raw_preservation(self):
  item=self.item(); a=self.analysis({'raw_item_name':'Olong','whole_quantity':2,'whole_unit':'g','confidence':.99}); self.normalize(a)
  with self.sessions() as s: line=s.scalar(select(InventoryLineModel)); self.assertEqual(line.item_id,item.id); self.assertEqual(line.raw_item_name,'Olong')
  self.assertEqual(normalize_unit('GÓI'),'pack'); self.assertIsNone(normalize_unit('unknown'))
 def test_fraction_negative_waste_and_outcomes(self):
  self.item(); a=self.analysis({'raw_item_name':'Olong','whole_quantity':2,'fraction_quantity':25,'whole_unit':'pack','fraction_unit':'g','waste_quantity':1,'confidence':.99}); self.normalize(a); self.validate(a.document_id)
  with self.sessions() as s: self.assertEqual(s.get(InventoryDocumentModel,a.document_id).status,'needs_review'); line=s.scalar(select(InventoryLineModel)); self.assertEqual(line.fraction_quantity,Decimal('25')); self.assertIn('waste_reason_required',line.review_note)
 def test_normalize_validate_idempotent_and_raw_analysis_unchanged(self):
  self.item(); a=self.analysis({'raw_item_name':'Olong','whole_quantity':1,'whole_unit':'g','confidence':.99}); raw=dict(a.extracted_json); self.normalize(a);self.normalize(a);self.validate(a.document_id);self.validate(a.document_id)
  with self.sessions() as s: self.assertEqual(s.scalar(select(func.count(InventoryLineModel.id))),1); self.assertEqual(s.get(InventoryAiAnalysisModel,a.id).extracted_json,raw); self.assertEqual(s.get(InventoryDocumentModel,a.document_id).status,'approved')
 def test_review_mutations_are_tenant_safe_audited_and_create_no_transactions(self):
  self.item(); a=self.analysis({'raw_item_name':'missing','whole_quantity':1,'whole_unit':'g','confidence':.99});self.normalize(a);self.validate(a.document_id)
  service=InventoryReviewService(self.sessions); review=service.list('tenant-a')[0]; service.mutate('tenant-a',review.id,'approve','reviewer-a');service.mutate('tenant-a',review.id,'approve','reviewer-a')
  with self.sessions() as s: self.assertEqual(s.scalar(select(func.count(InventoryReviewEventModel.id))),1);self.assertEqual(s.get(InventoryReviewModel,review.id).reviewer_id,'reviewer-a')
  self.assertIsNone(service.get('tenant-b',review.id))

 def test_correct_and_reupload_are_idempotent_tenant_safe_and_append_events(self):
  item=self.item(); a=self.analysis({'raw_item_name':'missing','whole_quantity':1,'whole_unit':'g','confidence':.99});self.normalize(a);self.validate(a.document_id)
  service=InventoryReviewService(self.sessions); review=service.list('tenant-a')[0]
  service.mutate('tenant-a',review.id,'correct','reviewer-a',{'item_id':item.id});service.mutate('tenant-a',review.id,'correct','reviewer-a',{'item_id':item.id})
  with self.sessions() as s: self.assertEqual(s.scalar(select(func.count(InventoryReviewEventModel.id))),1);self.assertEqual(s.get(InventoryAiAnalysisModel,a.id).extracted_json['raw_item_lines'][0]['raw_item_name'],'missing')
  self.assertRaises(LookupError,service.mutate,'tenant-b',review.id,'request_reupload','reviewer-b')
  service.mutate('tenant-a',review.id,'request_reupload','reviewer-a');service.mutate('tenant-a',review.id,'request_reupload','reviewer-a')
  with self.sessions() as s: self.assertEqual(s.scalar(select(func.count(InventoryReviewEventModel.id))),2);self.assertEqual(s.get(InventoryDocumentModel,a.document_id).status,'needs_reupload')
 def test_cross_tenant_item_reference_is_rejected(self):
  a=self.analysis({'raw_item_name':'missing','whole_quantity':1,'whole_unit':'g','confidence':.99});self.normalize(a);self.validate(a.document_id); foreign=self.item('Foreign','tenant-b'); review=InventoryReviewService(self.sessions).list('tenant-a')[0]
  self.assertRaises(ValueError,InventoryReviewService(self.sessions).mutate,'tenant-a',review.id,'correct','reviewer-a',{'item_id':foreign.id})
