from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.core.database import Base
from app.modules.visual_search.backfill_control import VisualSearchBackfillController


def test_start_resume_pause_cancel_are_tenant_bound_and_idempotent():
 engine=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool);Base.metadata.create_all(engine);session=Session(engine)
 try:
  control=VisualSearchBackfillController(session); first=control.start_or_resume(tenant_id="a",actor_id="u"); again=control.start_or_resume(tenant_id="a",actor_id="u")
  assert first.id==again.id and first.status=="running"
  assert control.pause(tenant_id="a",run_id=first.id).status=="paused"
  assert control.pause(tenant_id="a",run_id=first.id).status=="paused"
  assert control.start_or_resume(tenant_id="a").id==first.id
  assert control.cancel(tenant_id="a",run_id=first.id).status=="cancelled"
  assert control.cancel(tenant_id="a",run_id=first.id).status=="cancelled"
  import pytest
  with pytest.raises(LookupError): control.pause(tenant_id="b",run_id=first.id)
 finally: session.close();engine.dispose()
