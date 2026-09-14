from types import SimpleNamespace
import app.modules.visual_search.coverage as c
from app.modules.visual_search.coverage_repository import CoverageResource
from app.modules.visual_search.model_spec import VISUAL_SEARCH_BASELINE_DESCRIPTOR as d
def res(i="a",asset="x",eligible=True,unsupported=False):
 return CoverageResource(i,"s","google_drive","S","image/jpeg",asset,"a"*64,True,eligible,unsupported)
def doc(asset="x",**x):
 return {"tenant_id":"t","asset_id":asset,"content_sha256":"a"*64,"embedding_schema_version":d.embedding_schema_version,"encoder_name":d.encoder_name,"encoder_revision":d.encoder_revision,"preprocess_version":d.preprocess_version,"similarity":d.similarity,"is_deleted":False,"is_hidden":False,**x}
class I:
 def __init__(self,docs=None,fail=False):self.docs=docs or [];self.fail=fail;self.calls=0
 async def scan_projection_metadata(self,t):self.calls+=1;

def run(monkeypatch,rows,docs=None,fail=False):
 monkeypatch.setattr(c,"VisualCoverageResourceReader",lambda s:SimpleNamespace(resources=lambda t:rows))
 i=I(docs,fail)
 async def scan(t):
  i.calls+=1
  if fail:raise RuntimeError()
  return i.docs
 i.scan_projection_metadata=scan
 class S:
  def __enter__(self): return self
  def __exit__(self,*x): return False
  def scalars(self,*x): return SimpleNamespace(all=lambda:[])
 return c.VisualCoverageService(S,i).collect("t"),i
def test_service_classifies_missing_current_stale_and_once(monkeypatch):
 rows=[res("a","a"),res("b","b"),res("c","c"),res("d","a")]
 v,i=run(monkeypatch,rows,[doc("a"),doc("c",content_sha256="z"*64)])
 assert v.totals["visual_indexed_current"]==2 and v.totals["visual_index_missing"]==1 and v.totals["visual_index_stale"]==1 and i.calls==1
 assert sum(v.totals[k] for k in ("visual_indexed_current","visual_index_missing","visual_index_stale"))==v.totals["visual_eligible"]
def test_descriptor_mismatches_are_stale(monkeypatch):
 for key in ("embedding_schema_version","encoder_name","encoder_revision","preprocess_version","similarity"):
  v,_=run(monkeypatch,[res()],[doc(**{key:"old"})]);assert v.totals["visual_index_stale"]==1
def test_current_beats_stale_and_unavailable_is_unknown(monkeypatch):
 v,_=run(monkeypatch,[res()],[doc(content_sha256="z"*64),doc()]);assert v.totals["visual_indexed_current"]==1
 v,_=run(monkeypatch,[res(),res("u",None,False),res("g",None,False,True)],fail=True)
 assert v.index_state=="unavailable" and v.totals["visual_index_missing"] is None and v.totals["discovered_images"]==3
 assert v.ratios["eligible_visual_coverage"] is None and v.ratios["whole_resource_searchable"] is None
