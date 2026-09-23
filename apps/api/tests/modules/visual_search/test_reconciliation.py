from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
import app.modules.visual_search.reconciliation as reconciliation
from app.modules.visual_search.coverage_repository import CoverageResource
from app.modules.visual_search.model_spec import VISUAL_SEARCH_BASELINE_DESCRIPTOR as d


def resource(asset_id, source_id=None, hash_="a"*64, activity_at=None):
    return CoverageResource(source_id or "source-"+asset_id,"external","google_drive","Drive","image/jpeg",asset_id,hash_,True,True,False,activity_at)
def document(asset_id, hash_="a"*64, **changes):
    return {"tenant_id":"tenant-a","asset_id":asset_id,"content_sha256":hash_,"embedding_schema_version":d.embedding_schema_version,"encoder_name":d.encoder_name,"encoder_revision":d.encoder_revision,"preprocess_version":d.preprocess_version,"similarity":d.similarity,"is_deleted":False,"is_hidden":False,**changes}
class Index:
    def __init__(self,docs): self.docs=docs;self.calls=0
    async def scan_projection_metadata(self,tenant): self.calls+=1;return self.docs

def test_only_missing_and_stale_are_enqueued_once(monkeypatch):
    monkeypatch.setattr(reconciliation,"active_visual_queue_depth",lambda *_args,**_kwargs:0)
    rows=[resource("current"),resource("missing"),resource("stale",hash_="b"*64),resource("missing","second-source")]
    monkeypatch.setattr(reconciliation,"VisualCoverageResourceReader",lambda _:SimpleNamespace(resources=lambda _:rows))
    calls=[]
    monkeypatch.setattr(reconciliation,"enqueue_visual_index_sync",lambda processing,**kwargs: calls.append(kwargs) or True)
    index=Index([document("current"),document("stale","old"*16)])
    result=reconciliation.VisualSearchReconciliationService(object(),object(),index,settings=object()).reconcile(tenant_id="tenant-a")
    assert (result.scanned,result.current,result.missing,result.stale,result.enqueued,result.existing)==(3,1,1,1,2,0)
    assert result.checkpoint_asset_id=="stale" and not result.has_more
    assert {call["asset_id"] for call in calls}=={"missing","stale"} and index.calls==1

def test_es_failure_enqueues_nothing(monkeypatch):
    monkeypatch.setattr(reconciliation,"active_visual_queue_depth",lambda *_args,**_kwargs:0)
    monkeypatch.setattr(reconciliation,"VisualCoverageResourceReader",lambda _:SimpleNamespace(resources=lambda _:[resource("missing")]))
    class Broken:
        async def scan_projection_metadata(self,_): raise RuntimeError("down")
    monkeypatch.setattr(reconciliation,"enqueue_visual_index_sync",lambda *_ ,**__: pytest.fail("must not enqueue"))
    with pytest.raises(RuntimeError): reconciliation.VisualSearchReconciliationService(object(),object(),Broken(),settings=object()).reconcile(tenant_id="tenant-a")

def test_backfill_throttles_before_scanning_when_queue_cap_is_full(monkeypatch):
    monkeypatch.setattr(reconciliation,"active_visual_queue_depth",lambda *_args,**_kwargs:2)
    monkeypatch.setattr(
        reconciliation,
        "VisualCoverageResourceReader",
        lambda _: pytest.fail("coverage scan must not run while throttled"),
    )
    settings=SimpleNamespace(
        VISUAL_SEARCH_BACKFILL_MAX_QUEUED_JOBS=2,
        VISUAL_SEARCH_BACKFILL_MAX_SLICE_ASSETS=100,
        VISUAL_SEARCH_BACKFILL_RECENT_DAYS=30,
    )
    result=reconciliation.VisualSearchReconciliationService(
        object(),object(),object(),settings=settings
    ).reconcile(tenant_id="tenant-a")
    assert result.throttled is True
    assert result.enqueued == 0
    assert result.queue_depth == 2
    assert result.queue_capacity == 0
    assert result.has_more is True


def test_recent_backfill_jobs_outrank_archive_jobs_but_not_live_writes(monkeypatch):
    monkeypatch.setattr(reconciliation,"active_visual_queue_depth",lambda *_args,**_kwargs:0)
    now=datetime.now(timezone.utc)
    rows=[
        resource("archive",activity_at=now-timedelta(days=120)),
        resource("recent",activity_at=now-timedelta(days=2)),
    ]
    monkeypatch.setattr(
        reconciliation,
        "VisualCoverageResourceReader",
        lambda _:SimpleNamespace(resources=lambda _:rows),
    )
    calls=[]
    monkeypatch.setattr(
        reconciliation,
        "enqueue_visual_index_sync",
        lambda processing,**kwargs: calls.append(kwargs) or True,
    )
    settings=SimpleNamespace(
        VISUAL_SEARCH_BACKFILL_MAX_QUEUED_JOBS=250,
        VISUAL_SEARCH_BACKFILL_MAX_SLICE_ASSETS=100,
        VISUAL_SEARCH_BACKFILL_RECENT_DAYS=30,
    )
    reconciliation.VisualSearchReconciliationService(
        object(),object(),Index([]),settings=settings
    ).reconcile(tenant_id="tenant-a")
    priorities={call["asset_id"]:call["priority"] for call in calls}
    assert priorities["archive"] == 5
    assert priorities["recent"] == 15
    assert priorities["recent"] < 20
