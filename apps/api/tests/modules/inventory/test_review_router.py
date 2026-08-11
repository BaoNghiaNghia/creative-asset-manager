from __future__ import annotations
import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.core.database import Base
from app.modules.auth_persistence.model import TenantModel
from app.modules.assets.model import ExternalSourceModel  # noqa: F401
from app.modules.authorization.principal import CurrentPrincipal
from app.modules.inventory import router as inventory_router
from app.modules.inventory.persistence_model import InventoryDocumentModel, InventoryReviewModel, InventoryReviewEventModel
class ReviewRouterTest(unittest.TestCase):
 def setUp(self):
  self.engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool);Base.metadata.create_all(self.engine);self.factory=sessionmaker(self.engine,expire_on_commit=False)
  with self.factory() as s:
   s.add_all((TenantModel(id='a',name='A',slug='a'),TenantModel(id='b',name='B',slug='b')))
   for tenant,review in (('a','ra'),('b','rb')):
    doc=InventoryDocumentModel(id='d'+tenant,tenant_id=tenant,idempotency_key='d'+tenant,document_type='unclassified',status='needs_review',expected_pages=1,received_pages=1);s.add(doc);s.flush();s.add(InventoryReviewModel(id=review,tenant_id=tenant,document_id=doc.id,idempotency_key='r'+tenant,reason_code='unknown_item'))
   s.commit()
  self.app=FastAPI();self.app.include_router(inventory_router.router);self.client=TestClient(self.app)
  self.allow=CurrentPrincipal('user','a','m',None,frozenset(),frozenset({'inventory.read','inventory.review'}),False,'s','test')
  self.deny=CurrentPrincipal('user','a','m',None,frozenset(),frozenset({'inventory.read'}),False,'s','test')
 def request(self, principal, method,path,**kw):
  self.app.dependency_overrides.clear()
  for route in self.app.routes:
   if getattr(route,'path','').startswith('/api/inventory'):
    for permission_dependency in route.dependant.dependencies:
     for authenticated_dependency in permission_dependency.dependencies:
      self.app.dependency_overrides[authenticated_dependency.call]=lambda principal=principal: principal
  from unittest.mock import patch
  with patch('app.modules.inventory.router.SessionLocal',self.factory): return self.client.request(method,path,**kw)
 def test_list_detail_permission_and_tenant_scope(self):
  self.assertEqual(self.request(self.allow,'GET','/api/inventory/reviews').json()['items'][0]['id'],'ra')
  self.assertEqual(self.request(self.deny,'POST','/api/inventory/reviews/ra/approve').status_code,403)
  self.assertEqual(self.request(self.allow,'GET','/api/inventory/reviews/rb').status_code,404)
  self.assertEqual(self.request(self.allow,'GET','/api/inventory/reviews/missing').status_code,404)
 def test_approve_and_reupload_are_idempotent_and_audited(self):
  self.assertEqual(self.request(self.allow,'POST','/api/inventory/reviews/ra/approve').status_code,200);self.request(self.allow,'POST','/api/inventory/reviews/ra/approve')
  with self.factory() as s:self.assertEqual(s.scalar(select(func.count(InventoryReviewEventModel.id))),1)
  self.assertEqual(self.request(self.allow,'POST','/api/inventory/reviews/ra/request-reupload').status_code,200)
  with self.factory() as s:self.assertEqual(s.get(InventoryDocumentModel,'da').status,'needs_reupload');self.assertEqual(s.scalar(select(func.count(InventoryReviewEventModel.id))),2)
