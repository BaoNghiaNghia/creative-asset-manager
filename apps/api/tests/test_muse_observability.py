"""Isolated telemetry tests: no production credentials, database or renderer."""
from datetime import datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.modules.application_logs.model import ApplicationLogModel, LogApplicationModel
from app.modules.muse_bridge import observability as obs, router as muse
from app.modules.muse_bridge.model import CamMuseCommand, CamMuseJob
from app.modules.muse_bridge.worker_accounts import WorkerIdentity
from app.core.database import get_db

NOW = datetime(2026,11,24,10,0,tzinfo=timezone.utc)
TENANT = "test-muse-a"
TOKEN = "t"*64

def event(**kw):
    return {"eventId": str(uuid4()), "eventType": "WORKER_HEARTBEAT",
            "occurredAtUtc": NOW.isoformat(), "state": "idle", **kw}

def batch(*events):
    return obs.WorkerEventBatch.model_validate({"events": list(events)})

class ObservabilityTests(TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread":False},poolclass=StaticPool)
        for model in (LogApplicationModel,ApplicationLogModel,CamMuseJob,CamMuseCommand):
            model.__table__.create(self.engine)
        self.db = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)

    def report(self,*events,worker="muse-01",tenant=TENANT):
        return obs.ingest(self.db,tenant,worker,batch(*events),NOW)

    def diag(self,tenant=TENANT,now=NOW):
        return obs.diagnostics(self.db,tenant,recovery_enabled=True,
                               worker_ids=("muse-01","muse-02"),now=now)

    def test_ingestion_idempotent_conflict_and_identity(self):
        e=event()
        a=self.report(e)
        b=self.report(e)
        c=self.report({**e,"state":"rendering"})
        self.assertEqual(a["accepted"],[e["eventId"]])
        self.assertEqual(b["duplicates"],[e["eventId"]])
        self.assertEqual(c["rejected"][0]["code"],"EVENT_ID_CONFLICT")
        self.assertEqual(self.db.scalar(select(func.count()).select_from(ApplicationLogModel)),1)
        row=self.db.scalar(select(ApplicationLogModel))
        self.assertEqual(row.payload_json["workerId"],"muse-01")
        self.assertEqual(row.payload_json["origin"],"worker_reported")
        self.assertEqual(obs.aware(row.expires_at),NOW+timedelta(days=7))
        self.assertFalse(self.db.scalar(select(LogApplicationModel)).active)

    def test_diagnostics_separate_workers_and_tenants(self):
        self.report(event())
        self.report(event(state="rendering"),worker="muse-02",tenant="other")
        d=self.diag()
        self.assertEqual(d["workers"][0]["status"],"HEARTBEAT_RECENT")
        self.assertEqual(d["workers"][1]["status"],"NOT_OBSERVED")
        self.assertEqual(self.diag("other")["workers"][0]["status"],"NOT_OBSERVED")
        self.assertEqual(self.diag(now=NOW+timedelta(minutes=4))["workers"][0]["status"],"NO_RECENT_SIGNAL")

    def test_replayed_old_heartbeat_does_not_mark_online(self):
        self.report(event(occurredAtUtc=(NOW-timedelta(minutes=6)).isoformat()))
        self.assertEqual(self.diag()["workers"][0]["status"],"NO_RECENT_SIGNAL")

    def test_reject_future_and_expired(self):
        result=self.report(event(occurredAtUtc=(NOW+timedelta(hours=1)).isoformat()),
                           event(occurredAtUtc=(NOW-timedelta(days=8)).isoformat()))
        self.assertEqual(len(result["rejected"]),2)
        self.assertFalse(result["ok"])

    def test_no_free_text_or_token_and_timestamps(self):
        for invalid in (event(lease="secret"),event(workerId="muse-02"),
                        event(errorCode="secret-string"),
                        event(occurredAtUtc="2026-11-24T10:00:00")):
            with self.assertRaises(ValueError):
                batch(invalid)

    def test_recovery_diagnostics_and_expired_lease_counts(self):
        obs._write(self.db,TENANT,"CAM_RECOVERY_SWEEP","r1",{"newVideoCommands":2},NOW,NOW)
        self.db.commit()
        self.assertEqual(self.diag()["queueRecovery"]["status"],"RECENT")
        obs._write(self.db,TENANT,"CAM_RECOVERY_FAILED","r2",{},NOW+timedelta(seconds=1),NOW+timedelta(seconds=1))
        self.db.commit()
        self.assertEqual(self.diag(now=NOW+timedelta(seconds=2))["queueRecovery"]["status"],"LAST_SWEEP_FAILED")
        self.assertEqual(self.diag("other")["queueRecovery"]["status"],"NOT_OBSERVED")

    def test_api_route_and_error_redaction(self):
        cid=str(uuid4())
        self.assertEqual(obs.route_info("/api/v1/muse-bridge/commands/"+cid+"/claim"),
                         {"route":"command_claim","commandId":cid})
        self.assertIsNone(obs.route_info("/api/v1/muse-bridge/commands/secret/claim"))
        self.assertEqual(obs.safe_error(409,"Lease rejected secret-token"),"LEASE_REJECTED")
        self.assertEqual(obs.safe_error(422,{"secret":"token"}),"INVALID_CONTRACT")

    def test_cam_observation_persists_actual_status_separate_from_worker_heartbeat(self):
        with (patch.object(obs,"SessionLocal",side_effect=lambda:Session(self.engine)),
              patch.object(obs,"utcnow",return_value=NOW)):
            info={"route":"command_poll"}
            obs.observe_api(TENANT,"muse-01",info,"GET",200,20,"a")
            obs.observe_api(TENANT,"muse-01",info,"GET",200,30,"b")
            obs.observe_api(TENANT,"muse-01",{"route":"command_claim"},"POST",409,10,"c","Lease rejected secret")
        self.assertEqual(self.db.scalar(select(func.count()).select_from(ApplicationLogModel)),2)
        d=self.diag()["workers"][0]
        self.assertEqual(d["status"],"API_ACTIVE_HEARTBEAT_UNCONFIRMED")
        self.assertEqual(d["lastError"]["payload"]["errorCode"],"LEASE_REJECTED")
        self.assertNotIn("secret",str(d["lastError"]))

    def test_failed_business_result_is_warning_even_with_http_200(self):
        with (patch.object(obs,"SessionLocal",side_effect=lambda:Session(self.engine)),
              patch.object(obs,"utcnow",return_value=NOW)):
            obs.observe_api(TENANT,"muse-01",{"route":"command_complete"},"POST",
                200,12,"d",business_result={"outcome":"failed","phase":"create_video",
                "commandStatus":"pending","providerHttpStatus":503,"retryScheduled":True,
                "retryAfterSeconds":180,"listingId":"123456789","notes":"sensitive","lease":"secret"})
            obs.observe_api(TENANT,"muse-01",{"route":"reviews_verdict"},"POST",
                200,12,"e",business_result={"qaVerdict":"hold","jobStatus":"qa_hold"})
        rows=self.db.scalars(select(ApplicationLogModel)).all()
        self.assertEqual([r.level for r in rows],["warning","warning"])
        self.assertEqual(rows[0].payload_json["providerHttpStatus"],503)
        self.assertEqual(rows[0].payload_json["errorCode"],"PROVIDER_UNAVAILABLE")
        self.assertEqual(rows[0].payload_json["retryAfterSeconds"],180)
        self.assertNotIn("notes",rows[0].payload_json)
        self.assertNotIn("lease",rows[0].payload_json)
        self.assertEqual(rows[1].payload_json["errorCode"],"QA_HOLD")

    def test_http_auth_schema_and_no_business_mutation(self):
        app=FastAPI()
        app.include_router(muse.router)
        def get_test_db():
            with Session(self.engine) as db:
                yield db
        app.dependency_overrides[get_db]=get_test_db
        with (patch.object(muse,"_configured",return_value=(TOKEN,TENANT)),
              patch.object(muse,"authorize_worker",side_effect=lambda auth,**_:WorkerIdentity("legacy",True) if auth=="Bearer "+TOKEN else None),
              patch.object(muse,"_muse_dual_enabled",return_value=False),
              patch.object(muse,"_utc",return_value=NOW),
              patch.object(obs,"utcnow",return_value=NOW),
              patch.object(muse,"smart_queue_enabled",return_value=True),
              patch.object(muse,"renderer_circuit",return_value={"state":"closed"})):
            with TestClient(app) as client:
                path="/api/v1/muse-bridge/automation/events"
                headers={"Authorization":"Bearer "+TOKEN}
                e=event()
                self.assertEqual(client.post(path,json={"events":[e]}).status_code,401)
                self.assertEqual(client.post(path,json={"events":[event(lease="secret")]},headers=headers).status_code,422)
                r=client.post(path,json={"events":[e]},headers=headers)
                self.assertEqual(r.status_code,200,r.text)
                self.assertEqual(r.json()["accepted"],[e["eventId"]])
                r=client.get("/api/v1/muse-bridge/automation/diagnostics",headers=headers)
                self.assertEqual(r.status_code,200,r.text)
                self.assertEqual(r.json()["workers"][0]["status"],"HEARTBEAT_RECENT")
                self.assertEqual(r.json()["commands"]["byStatus"],{})
                self.assertEqual(r.json()["jobs"]["byStatus"],{})
                self.assertEqual(client.get(path,headers=headers).json()["items"][0]["eventType"],"WORKER_HEARTBEAT")