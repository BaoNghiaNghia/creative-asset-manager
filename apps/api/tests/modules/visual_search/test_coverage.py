from types import SimpleNamespace
from app.modules.visual_search.coverage import _ok,_ratio
from app.modules.visual_search.model_spec import VISUAL_SEARCH_BASELINE_DESCRIPTOR as d
def test_coverage_helpers():
 r=SimpleNamespace(asset_id="a",content_hash="x"*64)
 doc={"asset_id":"a","content_sha256":"x"*64,"embedding_schema_version":d.embedding_schema_version,"encoder_name":d.encoder_name,"encoder_revision":d.encoder_revision,"preprocess_version":d.preprocess_version,"similarity":d.similarity,"is_deleted":False,"is_hidden":False}
 assert _ok(doc,r) and not _ok({**doc,"encoder_revision":"old"},r)
 assert _ratio(0,0)==0.0
