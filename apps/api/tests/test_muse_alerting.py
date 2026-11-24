from datetime import datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.modules.application_logs.model import ApplicationLogModel, LogApplicationModel
from app.modules.muse_bridge import alerting as a, observability as o, monitor, router as muse
from app.modules.muse_bridge.model import CamMuseCommand, CamMuseJob
from app.modules.muse_bridge.worker_accounts import WorkerIdentity

NOW=datetime(2026,11,24,16,0,tzinfo=timezone.utc)
ALERT={"id":"muse-01:heartbeat","severity":"warning","message":"Missing runner heartbeat"}
TENANT="test-alert-tenant"

class Tests(TestCase):
    def test_debounce_open_and_recovery(self):
        state,opened,resolved=a.evaluate({},[ALERT],NOW)
        self.assertEqual(opened,[])
        state,opened,resolved=a.evaluate(state,[ALERT],NOW+timedelta(seconds=120))
        self.assertEqual(opened,[ALERT["id"]])
        state,opened,resolved=a.evaluate(state,[],NOW+timedelta(seconds=240))
        self.assertEqual(resolved,[])
        state,opened,resolved=a.evaluate(state,[],NOW+timedelta(seconds=360))
        self.assertEqual(resolved,[ALERT["id"]])
        self.assertEqual(state["active"],{})

    def test_transient_errors_and_generic_expiry_not_push_incidents(self):
        alerts=[{"id":"muse-01:recent-error","severity":"warning","message":"502"},
                {"id":"generic:expired","severity":"warning","message":"old research"},
                {"id":"muse-02:standby","severity":"info","message":"standby"}]
        state,_,_=a.evaluate({},alerts,NOW)
        state,opened,_=a.evaluate(state,alerts,NOW+timedelta(seconds=120))
        self.assertEqual(opened,[])
        self.assertEqual(state["active"],{})

    def test_delivery_grouping_dedup_and_backoff(self):
        state={"active":{ALERT["id"]:ALERT}}
        self.assertIsNone(a.reserve_delivery(state,NOW,False))
        r=a.reserve_delivery(state,NOW,True)
        self.assertIn("Missing runner heartbeat",r["message"])
        self.assertIsNone(a.reserve_delivery(state,NOW,True))
        self.assertTrue(a.finish_delivery(state,r,NOW,False,1800))
        self.assertIsNone(a.reserve_delivery(state,NOW+timedelta(seconds=1799),True))
        r2=a.reserve_delivery(state,NOW+timedelta(seconds=1801),True)
        self.assertTrue(a.finish_delivery(state,r2,NOW+timedelta(seconds=1802),True))
        self.assertIsNone(a.reserve_delivery(state,NOW+timedelta(hours=2),True))
        state["active"]={}
        r3=a.reserve_delivery(state,NOW+timedelta(hours=2),True)
        self.assertIn("Resolved",r3["message"])

    def test_config_opt_in(self):
        with patch.dict(a.os.environ,{},clear=True):
            self.assertIsNone(a.telegram_config())
        with patch.dict(a.os.environ,{"CAM_MUSE_TELEGRAM_ENABLED":"true","CAM_MUSE_TELEGRAM_BOT_TOKEN":"bad","CAM_MUSE_TELEGRAM_CHAT_ID":"12345"},clear=True):
            self.assertIsNone(a.telegram_config())
        with patch.dict(a.os.environ,{"CAM_MUSE_TELEGRAM_ENABLED":"true","CAM_MUSE_TELEGRAM_BOT_TOKEN":"12345:"+"x"*30,"CAM_MUSE_TELEGRAM_CHAT_ID":"-12345"},clear=True):
            self.assertIsNotNone(a.telegram_config())

    def test_state_is_private_and_durable(self):
        engine=create_engine("sqlite+pysqlite:///:memory:",connect_args={"check_same_thread":False},poolclass=StaticPool)
        self.addCleanup(engine.dispose)
        for model in (LogApplicationModel,ApplicationLogModel,CamMuseJob,CamMuseCommand):
            model.__table__.create(engine)
        with Session(engine) as db:
            row=a.state_row(db,TENANT,NOW,create=True)
            s,_,_=a.evaluate({},[ALERT],NOW)
            a.save(row,s,NOW)
            db.commit()
        with Session(engine) as db:
            row=a.state_row(db,TENANT,NOW+timedelta(seconds=120))
            s,opened,_=a.evaluate(row.payload_json,[ALERT],NOW+timedelta(seconds=120))
            self.assertEqual(opened,[ALERT["id"]])
            a.save(row,s,NOW+timedelta(seconds=120))
            db.commit()
            self.assertEqual(o.timeline(db,TENANT,NOW+timedelta(seconds=120)),[])
            self.assertIsNone(a.state_row(db,"other",NOW))
            self.assertEqual(a.summary(db,TENANT,NOW+timedelta(seconds=121))["activeIncidentIds"],[ALERT["id"]])

    def test_config_poll_is_not_operational_activity(self):
        engine=create_engine("sqlite+pysqlite:///:memory:")
        self.addCleanup(engine.dispose)
        for model in (LogApplicationModel,ApplicationLogModel,CamMuseJob,CamMuseCommand):
            model.__table__.create(engine)
        with Session(engine) as db:
            o._write(db,TENANT,"CAM_API_RESULT","config",{"workerId":"muse-02","route":"worker_config","httpStatus":200},NOW,NOW)
            db.commit()
            report=o.diagnostics(db,TENANT,recovery_enabled=True,worker_ids=("muse-02",),now=NOW)
            w=report["workers"][0]
            self.assertEqual(w["apiSignalKind"],"config_only")
            self.assertIsNone(w["operationalApiActivityAgeSeconds"])
            self.assertTrue(any("only reads configuration" in x["message"] for x in monitor.derive_alerts(report,NOW)))
            o._write(db,TENANT,"CAM_API_RESULT","poll",{"workerId":"muse-02","route":"command_poll","httpStatus":200},NOW,NOW)
            db.commit()
            self.assertEqual(o.diagnostics(db,TENANT,recovery_enabled=True,worker_ids=("muse-02",),now=NOW)["workers"][0]["apiSignalKind"],"operational")

    def test_runner_integration_is_authenticated_and_read_only(self):
        app=FastAPI()
        app.include_router(muse.router)
        with patch.object(muse,"_configured",return_value=("x"*64,TENANT)),patch.object(muse,"authorize_worker",return_value=None):
            client=TestClient(app)
            path="/api/v1/muse-bridge/automation/runner-integration"
            self.assertEqual(client.get(path).status_code,401)
            app.dependency_overrides[muse._muse]=lambda:TENANT
            response=client.get(path)
            self.assertEqual(response.status_code,200)
            payload=response.json()
            self.assertFalse(payload["automaticExecution"])
            self.assertTrue(payload["requiresAuthorizedOwnerInstall"])
            self.assertEqual(len(payload["sha256"]),64)
            self.assertIn("class ",payload["pythonSource"])