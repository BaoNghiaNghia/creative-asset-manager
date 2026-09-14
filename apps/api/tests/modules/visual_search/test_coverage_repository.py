from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.core.database import Base
from app.modules.assets.model import AssetModel,AssetSourceLinkModel,ExternalSourceModel,SourceAssetModel
from app.modules.visual_search.coverage_repository import VisualCoverageResourceReader

def row(id,source,mime,tenant="t"): return SourceAssetModel(id=id,tenant_id=tenant,external_source_id=source,external_asset_id=id,mime_type=mime)
def test_resource_reader_preserves_source_resources_and_eligibility():
 e=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool);Base.metadata.create_all(e)
 with Session(e) as s:
  s.add_all([ExternalSourceModel(id="s1",tenant_id="t",source_key="1",source_type="google_drive",display_name="One"),ExternalSourceModel(id="s2",tenant_id="t",source_key="2",source_type="onedrive"),row("a","s1","image/jpeg"),row("b","s2","image/jpeg"),row("u","s1","image/gif"),row("v","s1","video/mp4"),AssetModel(id="x",tenant_id="t",content_hash="a"*64)])
  s.add_all([AssetSourceLinkModel(id="la",tenant_id="t",asset_id="x",source_asset_id="a"),AssetSourceLinkModel(id="lb",tenant_id="t",asset_id="x",source_asset_id="b")]);s.commit()
  rows=VisualCoverageResourceReader(s).resources("t")
  assert [r.source_asset_id for r in rows]==["a","b","u"]
  assert sum(r.imported for r in rows)==2 and sum(r.eligible for r in rows)==2 and sum(r.unsupported for r in rows)==1
  assert rows[0].display_name=="One" and rows[1].source_type=="onedrive"
