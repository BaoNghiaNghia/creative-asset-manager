from types import SimpleNamespace
import pytest
import app.modules.visual_search.reconciliation as reconciliation
from app.modules.visual_search.coverage_repository import CoverageResource
from app.modules.visual_search.model_spec import VISUAL_SEARCH_BASELINE_DESCRIPTOR as d


def resource(asset_id, source_id=None, hash_="a"*64):
    return CoverageResource(source_id or "source-"+asset_id,"external","google_drive","Drive","image/jpeg",asset_id,hash_,True,True,False)
def document(asset_id, hash_="a"*64, **changes):
    return {"tenant_id":"tenant-a","asset_id":asset_id,"content_sha256":hash_,"embedding_schema_version":d.embedding_schema_version,"encoder_name":d.encoder_name,"encoder_revision":d.encoder_revision,"preprocess_version":d.preprocess_version,"similarity":d.similarity,"is_deleted":False,"is_hidden":False,**changes}
class Index:
    def __init__(self,docs): self.docs=docs;self.calls=0
    async def scan_projection_metadata(self,tenant): self.calls+=1;return self.docs

def test_only_missing_and_stale_are_enqueued_once(monkeypatch):
    rows=[resource("current"),resource("missing"),resource("stale",hash_="b"*64),resource("missing","second-source")]
    monkeypatch.setattr(reconciliation,"VisualCoverageResourceReader",lambda _:SimpleNamespace(resources=lambda _:rows))
    calls=[]
    monkeypatch.setattr(reconciliation,"enqueue_visual_index_sync",lambda processing,**kwargs: calls.append(kwargs) or True)
    index=Index([document("current"),document("stale","old"*16)])
    result=reconciliation.VisualSearchReconciliationService(object(),object(),index,settings=object()).reconcile(tenant_id="tenant-a")
    assert (result.current,result.missing,result.stale,result.enqueued,result.existing)==(1,1,1,2,0)
    assert {call["asset_id"] for call in calls}=={"missing","stale"} and index.calls==1

def test_es_failure_enqueues_nothing(monkeypatch):
    monkeypatch.setattr(reconciliation,"VisualCoverageResourceReader",lambda _:SimpleNamespace(resources=lambda _:[resource("missing")]))
    class Broken:
        async def scan_projection_metadata(self,_): raise RuntimeError("down")
    monkeypatch.setattr(reconciliation,"enqueue_visual_index_sync",lambda *_ ,**__: pytest.fail("must not enqueue"))
    with pytest.raises(RuntimeError): reconciliation.VisualSearchReconciliationService(object(),object(),Broken(),settings=object()).reconcile(tenant_id="tenant-a")
