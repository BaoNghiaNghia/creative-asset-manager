"""Isolated cooldown storage and gate tests; never call a real provider."""
from datetime import datetime, timedelta, timezone
import errno
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from fastapi import FastAPI, Request, HTTPException
from fastapi.testclient import TestClient
from app.modules.muse_bridge import worker_failover as w, router as m

NOW = datetime(2026,11,26,0,0,tzinfo=timezone.utc)
DAILY = "video_daily_budget_exhausted"

class CooldownPersistenceTests(TestCase):
    def setUp(self):
        td=TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.root=Path(td.name)
        self.ops=self.root/"ops"
        self.ops.mkdir()
        self.legacy=self.ops/"muse-worker-state"
        self.legacy.mkdir(mode=0o700)
        self.state=self.root/"state"
        self.state.mkdir()
        self.env={"CAM_MUSE_WORKER_REGISTRATION_FILE":str(self.ops/"registry.json"),
                  "STATE_DIRECTORY":str(self.state)}

    def report(self,now=NOW,delay=3600,deadline=None,reason=DAILY,env=None,worker="muse-01"):
        return w.report_provider_cooldown(self.env if env is None else env,worker,
            provider_status=429,retry_after_seconds=delay,reason=reason,now=now,cooldown_until=deadline)

    def test_systemd_state_not_registration_tree(self):
        result=self.report()
        self.assertTrue(result["cooldownActive"])
        self.assertEqual(result["reason"],DAILY)
        state=self.state/"muse-worker-state"
        self.assertEqual(state.stat().st_mode & 0o777,0o700)
        self.assertEqual((state/"muse-01.json").stat().st_mode & 0o777,0o600)
        self.assertFalse((self.legacy/".lock").exists())
        self.assertEqual(w.cooldown_status(self.env,"muse-01",now=NOW),result)
        self.assertFalse(w.cooldown_status(self.env,"muse-02",now=NOW)["cooldownActive"])

    def test_preserve_existing_legacy_deadline(self):
        old=self.report(env={"CAM_MUSE_WORKER_REGISTRATION_FILE":self.env["CAM_MUSE_WORKER_REGISTRATION_FILE"]},delay=7200)
        self.assertEqual(w.cooldown_status(self.env,"muse-01",now=NOW),old)
        self.assertEqual(self.report(delay=180)["cooldownUntilUtc"],old["cooldownUntilUtc"])

    def test_cron_relative_daily_replay_does_not_slide(self):
        first=self.report()
        replay=self.report(now=NOW+timedelta(minutes=2))
        self.assertEqual(first["cooldownUntilUtc"],replay["cooldownUntilUtc"])
        self.assertEqual(replay["secondsRemaining"],3480)

    def test_absolute_replay_and_expired_replay(self):
        until=NOW+timedelta(minutes=15)
        first=self.report(deadline=until)
        second=self.report(now=NOW+timedelta(minutes=5),deadline=until)
        self.assertEqual(second["cooldownUntilUtc"],first["cooldownUntilUtc"])
        self.assertEqual(second["secondsRemaining"],600)
        expired=self.report(now=until+timedelta(seconds=1),deadline=until)
        self.assertFalse(expired["cooldownActive"])
        later=self.report(now=until+timedelta(seconds=2),delay=600)
        self.assertTrue(later["cooldownActive"])

    def test_new_actual_absolute_deadline_can_extend_not_shorten(self):
        first=self.report(deadline=NOW+timedelta(minutes=15))
        longer=self.report(deadline=NOW+timedelta(minutes=30))
        shorter=self.report(deadline=NOW+timedelta(minutes=10))
        self.assertNotEqual(first["cooldownUntilUtc"],longer["cooldownUntilUtc"])
        self.assertEqual(shorter["cooldownUntilUtc"],longer["cooldownUntilUtc"])

    def test_bad_absolute_deadline(self):
        for deadline in (NOW.replace(tzinfo=None),NOW+timedelta(days=2)):
            with self.assertRaises(ValueError):
                self.report(deadline=deadline)

    def test_symlink_private_dir_rejected(self):
        (self.state/"muse-worker-state").symlink_to(self.legacy,target_is_directory=True)
        with self.assertRaises(w.WorkerAccountConfigError):
            self.report()

    def test_read_only_write_is_controlled_failure(self):
        w._directory(self.env)
        with patch.object(w.os,"open",side_effect=OSError(errno.EROFS,"read-only")):
            with self.assertRaises(w.WorkerAccountConfigError):
                self.report()
        self.assertFalse(w.cooldown_status(self.env,"muse-01",now=NOW)["cooldownActive"])

    def test_actual_http_reports_503_then_200(self):
        app=FastAPI()
        app.include_router(m.router)
        def auth(request:Request):
            request.state.muse_worker_id="muse-01"
            return "test-tenant"
        app.dependency_overrides[m._muse]=auth
        with TestClient(app,raise_server_exceptions=False) as c, patch.dict(os.environ,self.env), patch.object(m,"_muse_dual_enabled",return_value=True):
            path="/api/v1/muse-bridge/workers/cooldown"
            data={"providerStatus":429,"reason":DAILY,"retryAfterSeconds":600}
            with patch.object(w.os,"open",side_effect=OSError(errno.EROFS,"read-only")):
                r=c.post(path,json=data)
                self.assertEqual(r.status_code,503,r.text)
            r=c.post(path,json=data)
            self.assertEqual(r.status_code,200,r.text)
            self.assertTrue(r.json()["cooldown"]["cooldownActive"])
            self.assertFalse(r.json()["activeVideoLeaseTransferred"])

    def test_daily_cooldown_blocks_video_allows_review(self):
        self.report()
        req=Request({"type":"http","headers":[]})
        req.state.muse_worker_id="muse-01"
        with patch.dict(os.environ,self.env), patch.object(m,"_muse_dual_enabled",return_value=True), patch.object(w,"_now",return_value=NOW):
            with self.assertRaises(HTTPException) as exc:
                m._assert_muse_executor_ready(req)
            self.assertEqual(exc.exception.status_code,429)
            self.assertEqual(exc.exception.headers["Retry-After"],"3600")
            m._assert_muse_executor_ready(req,review_only=True)
            self.assertIsNone(m._muse_poll_gate(req))
            self.assertTrue(req.state.muse_review_only)
