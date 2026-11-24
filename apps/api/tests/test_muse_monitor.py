import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient
from app.core.database import get_db
from app.modules.etsy_spy import router as spy
from app.modules.muse_bridge import monitor, router as muse

NOW=datetime(2026,11,24,16,13,tzinfo=timezone.utc)
def make_report():
    return {"workers":[{"workerId":"muse-01","status":"API_ACTIVE_HEARTBEAT_UNCONFIRMED",
                         "executionReadiness":{"ready":True}},
                       {"workerId":"muse-02","status":"NO_RECENT_SIGNAL",
                         "executionReadiness":{"ready":False}}],
            "queueRecovery":{"enabled":True,"status":"RECENT"},
            "commands":{"expiredClaims":1,"missingLeaseExpiry":0},
            "jobs":{"expiredClaims":0,"missingLeaseExpiry":0},
            "rendererCircuit":{"state":"closed"}}
class MonitorTests(unittest.TestCase):
    def test_worker_state_is_not_faked(self):
        a=monitor.derive_alerts(make_report(),NOW)
        ids={x["id"] for x in a}
        self.assertIn("muse-01:heartbeat",ids)
        self.assertIn("muse-02:standby",ids)
        self.assertNotIn("muse-02:signal",ids)
        self.assertIn("commands:expired",ids)

    def test_recovery_and_cooldown_distinct(self):
        r=make_report()
        r["queueRecovery"]["status"]="STALE"
        r["rendererCircuit"]["state"]="open"
        r["workers"][0]["cooldown"]={"cooldownActive":True}
        a={x["id"]:x["severity"] for x in monitor.derive_alerts(r,NOW)}
        self.assertEqual(a["queue-recovery"],"error")
        self.assertEqual(a["renderer-circuit"],"warning")
        self.assertEqual(a["muse-01:cooldown"],"info")
    def test_old_errors_not_active(self):
        r=make_report()
        r["workers"][0]["lastError"]={"occurredAtUtc":(NOW-timedelta(minutes=20)).isoformat(),"payload":{"errorCode":"COMMAND_FAILED"}}
        self.assertNotIn("muse-01:recent-error",{a["id"] for a in monitor.derive_alerts(r,NOW)})
        r["workers"][0]["lastError"]["occurredAtUtc"]=NOW.isoformat()
        self.assertIn("muse-01:recent-error",{a["id"] for a in monitor.derive_alerts(r,NOW)})

    def test_tenant_and_http_permission_boundary(self):
        app=FastAPI()
        app.include_router(spy.router)
        app.dependency_overrides[get_db]=lambda:object()
        path="/api/v1/etsy-spy/muse-monitor"
        with patch.object(muse,"_configured",return_value=("test-not-a-credential","tenant-a")),patch.object(muse,"muse_automation_diagnostics",return_value=make_report()) as d,patch("app.modules.muse_bridge.alerting.summary",return_value={"telegram":{"status":"not_configured"}}):
            client=TestClient(app)
            # Permission dependency still rejects unauthenticated sessions.
            self.assertIn(client.get(path).status_code,(401,403))
            app.dependency_overrides[spy.READ]=lambda:SimpleNamespace(active_tenant_id="tenant-b")
            self.assertEqual(client.get(path).status_code,404)
            d.assert_not_called()
            app.dependency_overrides[spy.READ]=lambda:SimpleNamespace(active_tenant_id="tenant-a")
            response=client.get(path)
            self.assertEqual(response.status_code,200,response.text)
            self.assertTrue(response.json()["readOnly"])
            self.assertNotIn("test-not-a-credential",response.text)
            self.assertEqual(d.call_args.kwargs["tenant"],"tenant-a")
if __name__=="__main__":unittest.main()