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

def test_eligible_resources_page_is_asset_cursor_bounded_and_deduplicated():
 e=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool);Base.metadata.create_all(e)
 with Session(e) as s:
  s.add(ExternalSourceModel(id="s1",tenant_id="t",source_key="1",source_type="google_drive"))
  s.add_all([
   SourceAssetModel(id="sa-0",tenant_id="t",external_source_id="s1",external_asset_id="sa-0",filename="a.gif",mime_type="image/gif"),
   SourceAssetModel(id="sa",tenant_id="t",external_source_id="s1",external_asset_id="sa",filename="a.jpg",mime_type="image/jpeg"),
   SourceAssetModel(id="sa-copy",tenant_id="t",external_source_id="s1",external_asset_id="sa-copy",filename="a-copy.jpg",mime_type="application/octet-stream"),
   SourceAssetModel(id="sb",tenant_id="t",external_source_id="s1",external_asset_id="sb",filename="b.png",mime_type="application/octet-stream"),
  ])
  s.add_all([
   AssetModel(id="asset-a",tenant_id="t",content_hash="a"*64),
   AssetModel(id="asset-b",tenant_id="t",content_hash="b"*64),
  ]);s.flush()
  s.add_all([
   AssetSourceLinkModel(id="la-0",tenant_id="t",asset_id="asset-a",source_asset_id="sa-0"),
   AssetSourceLinkModel(id="la",tenant_id="t",asset_id="asset-a",source_asset_id="sa"),
   AssetSourceLinkModel(id="la-copy",tenant_id="t",asset_id="asset-a",source_asset_id="sa-copy"),
   AssetSourceLinkModel(id="lb",tenant_id="t",asset_id="asset-b",source_asset_id="sb"),
  ]);s.commit()
  reader=VisualCoverageResourceReader(s)
  first,has_more=reader.eligible_resources_page("t",limit=1)
  assert [item.asset_id for item in first]==["asset-a"]
  assert has_more is True
  second,has_more=reader.eligible_resources_page("t",after_asset_id="asset-a",limit=1)
  assert [item.asset_id for item in second]==["asset-b"]
  assert has_more is False
