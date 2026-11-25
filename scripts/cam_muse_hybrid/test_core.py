"""Offline regression for the Hybrid V1 integration core.

All transport responses are explicit fixtures. No CAM claims, provider
requests, account login, real QA verdicts, or renderer usage.
"""
from base64 import b64decode
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch

from core import (
    POLICY, PNG, DISCOVERY_PATH, RenderIdentity, RenderIntent, Journal,
    GatewayAdapter, Coordinator, ContinuousDispatcher, DispatchResult,
    HybridBlocked, digest, fingerprint,
)

ORIGINAL=PNG+b"offline-original-fixture"
HERO=PNG+b"offline-hero-fixture"
TASK="task_"+"a"*24
CMD="11111111-1111-4111-8111-111111111111"
PROMPT="Offline fixture, not an actual render."
MEDIA_SHA="b"*64


def make_intent(**identity_changes):
    return RenderIntent(RenderIdentity(**{
        "tenant":"test-tenant", "listing_id":"123456789",
        "artwork_sha256":digest(ORIGINAL),"garment":"C1717","ratio":"9:16",
        "story_version":"CAM_STORY_CHARACTER_LITE_V1","take_index":1,
        "generation_revision":"initial",**identity_changes}),
        worker="mock-worker",command_id=CMD,contract_sha256="c"*64,
        hero_sha256=digest(HERO),duration_seconds=6,backend_id="mock-backend")


def ready_intent(action="create_video"):
    return {"listingId":"123456789","phase":action,"executionContract":{
        "authority":"CAM","nextAction":action,"commandClaimHeaders":{},
    }}


class MockGateway:
    def __init__(self):
        self.calls=[]
        self.submit_result=(202,{"id":TASK,"status":"queued"},{})
        self.status_result=(200,{"id":TASK,"status":"running","partial":False,"media":[]},{})
    def request(self,method,path,body):
        self.calls.append((method,path,body))
        result=self.submit_result if method=="POST" else self.status_result
        if isinstance(result,Exception):
            raise result
        return result
    @property
    def posts(self):
        return sum(c[0]=="POST" for c in self.calls)


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.mock=MockGateway()
        self.intent=make_intent()
        self.key=self.intent.identity.key

    def coordinator(self,journal,verified=True,ready=True):
        return Coordinator(journal,GatewayAdapter(
            self.mock.request,model="mock-video",compatibility_verified=verified),
            lambda _:ready)

    def submit(self,c,**changes):
        return c.submit(self.intent,**{"original_png":ORIGINAL,"hero_png":HERO,
            "hero_artwork_sha":self.intent.identity.artwork_sha256,
            "prompt":PROMPT,**changes})

    def make_result_ready(self,c):
        self.submit(c)
        self.mock.status_result=(200,{"id":TASK,"status":"succeeded",
            "partial":False,"media":[{"kind":"video","url":"fixture://video"}]},{})
        return c.poll(self.key)

    def test_key_is_revision_scoped_not_listing_only(self):
        for change in ({"artwork_sha256":"d"*64},{"ratio":"16:9"},
                       {"story_version":"new-policy"},{"take_index":2},
                       {"generation_revision":"authorized-regenerate-1"},{"tenant":"other"}):
            self.assertNotEqual(make_intent(**change).identity.key,self.key)
        self.assertEqual(make_intent().identity.key,self.key)

    def test_persist_before_post_and_bind_exact_reference(self):
        with Journal(self.temp.name) as j:
            def request(method,path,body):
                self.assertEqual(method,"POST")
                self.assertEqual(path,"/v1/tasks")
                stored=json.loads(j.path.read_text())["records"][self.key]
                self.assertEqual(stored["state"],"submitting")
                self.assertEqual(stored["request_sha256"],fingerprint(body))
                self.assertEqual(b64decode(body["images"][0].split(",",1)[1]),HERO)
                self.assertEqual(body["idempotency_key"],self.key)
                self.assertEqual(body["ratio"],"9:16")
                self.assertEqual(body["duration"],6)
                self.assertTrue(body["new_chat"])
                return self.mock.request(method,path,body)
            c=Coordinator(j,GatewayAdapter(request,model="mock-video",
                          compatibility_verified=True),lambda _:True)
            self.assertEqual(self.submit(c)["state"],"submitted")
            self.assertEqual(self.mock.posts,1)

    def test_duplicate_submit_coalesces_even_after_restart(self):
        with Journal(self.temp.name) as j:
            c=self.coordinator(j)
            self.submit(c)
            self.assertEqual(self.submit(c)["task_id"],TASK)
        with Journal(self.temp.name) as j:
            self.assertEqual(self.submit(self.coordinator(j))["state"],"submitted")
        self.assertEqual(self.mock.posts,1)

    def test_race_two_threads_one_post(self):
        with Journal(self.temp.name) as j:
            c=self.coordinator(j)
            results=[]
            threads=[Thread(target=lambda:results.append(self.submit(c))) for _ in range(2)]
            for t in threads:t.start()
            for t in threads:t.join()
            self.assertEqual(len(results),2)
            self.assertEqual(self.mock.posts,1)

    def test_process_lock_and_unlock(self):
        with Journal(self.temp.name):
            with self.assertRaisesRegex(HybridBlocked,"WORKER_ALREADY_RUNNING"):
                with Journal(self.temp.name):pass
        with Journal(self.temp.name) as j:self.assertIsNone(j.active())

    def test_lost_post_response_is_not_resent(self):
        self.mock.submit_result=TimeoutError("offline timeout")
        with Journal(self.temp.name) as j:
            self.assertEqual(self.submit(self.coordinator(j))["state"],"unknown")
        with Journal(self.temp.name) as j:
            c=self.coordinator(j)
            self.assertEqual(self.submit(c)["state"],"unknown")
            with self.assertRaisesRegex(HybridBlocked,"TASK_ID_UNKNOWN"):
                c.poll(self.key)
        self.assertEqual(self.mock.posts,1)

    def test_crash_at_submission_boundary_no_resend(self):
        with Journal(self.temp.name) as j:
            c=self.coordinator(j)
            payload=c.gateway.build_request(self.intent,PROMPT,HERO)
            j.prepare(self.intent,fingerprint(payload))
            j.transition(self.key,{"prepared"},"submitting")
        with Journal(self.temp.name) as j:
            self.assertEqual(self.submit(self.coordinator(j))["state"],"submitting")
        self.assertEqual(self.mock.posts,0)

    def test_lost_task_id_requires_exact_mapping(self):
        self.mock.submit_result=TimeoutError()
        with Journal(self.temp.name) as j:
            c=self.coordinator(j)
            self.submit(c)
            with self.assertRaisesRegex(HybridBlocked,"TASK_MAPPING_UNVERIFIED"):
                c.bind_reconciled_task(self.key,TASK,lambda *_:False)
            self.assertEqual(c.bind_reconciled_task(self.key,TASK,lambda record,task:
                record["intent"]["worker"]=="mock-worker" and task==TASK)["task_id"],TASK)
            self.assertEqual(c.poll(self.key)["state"],"rendering")
            self.assertEqual(self.mock.posts,1)

    def test_changed_prompt_or_owner_never_reuses_key(self):
        with Journal(self.temp.name) as j:
            c=self.coordinator(j)
            self.submit(c)
            with self.assertRaisesRegex(HybridBlocked,"KEY_ALREADY_BOUND"):
                self.submit(c,prompt="different prompt")
            self.intent=replace(self.intent,worker="other")
            with self.assertRaisesRegex(HybridBlocked,"KEY_ALREADY_BOUND"):
                self.submit(c)
        self.assertEqual(self.mock.posts,1)

    def test_new_sha_waits_while_old_render_unknown(self):
        self.mock.submit_result=TimeoutError()
        with Journal(self.temp.name) as j:
            c=self.coordinator(j)
            self.submit(c)
            original2=PNG+b"new-revision"
            self.intent=make_intent(artwork_sha256=digest(original2))
            with self.assertRaisesRegex(HybridBlocked,"UNRESOLVED_RENDER_EXISTS"):
                self.submit(c,original_png=original2)
        self.assertEqual(self.mock.posts,1)

    def test_unverified_renderer_or_cam_gate_no_post(self):
        with Journal(self.temp.name) as j:
            with self.assertRaisesRegex(HybridBlocked,"COMPATIBILITY_UNVERIFIED"):
                self.submit(self.coordinator(j,verified=False))
            with self.assertRaisesRegex(HybridBlocked,"CAM_READINESS_NOT_VERIFIED"):
                self.submit(self.coordinator(j,ready=False))
            self.assertIsNone(j.active())
        self.assertEqual(self.mock.posts,0)

    def test_wrong_original_or_hero_blocks_before_submit(self):
        with Journal(self.temp.name) as j:
            c=self.coordinator(j)
            for changes,code in (({"original_png":PNG+b"thumbnail"},"ARTWORK_UNAVAILABLE"),
                                  ({"hero_png":ORIGINAL},"HERO_BINDING_INVALID"),
                                  ({"hero_artwork_sha":"f"*64},"HERO_BINDING_INVALID")):
                with self.assertRaisesRegex(HybridBlocked,code):
                    self.submit(c,**changes)
            self.assertIsNone(j.active())
        self.assertEqual(self.mock.posts,0)

    def test_429_does_not_resend_and_retains_retry_after(self):
        self.mock.submit_result=(429,{},{"Retry-After":"600"})
        with Journal(self.temp.name) as j:
            c=self.coordinator(j)
            r=self.submit(c)
            self.assertEqual(r["state"],"unknown")
            self.assertEqual(r["http_status"],429)
            self.assertEqual(r["retry_after_seconds"],600)
            self.submit(c)
        self.assertEqual(self.mock.posts,1)

    def test_failed_cancelled_and_wrong_task_are_not_safe_terminal(self):
        with Journal(self.temp.name) as j:
            c=self.coordinator(j)
            self.submit(c)
            for status in ("failed","cancelled"):
                self.mock.status_result=(200,{"id":TASK,"status":status},{})
                self.assertEqual(c.poll(self.key)["state"],"unknown")
            self.mock.status_result=(200,{"id":"task_"+"b"*24,"status":"running"},{})
            self.assertEqual(c.poll(self.key)["state"],"unknown")
            with self.assertRaisesRegex(HybridBlocked,"TERMINAL_STATE_UNVERIFIED"):
                c.close_confirmed_terminal(self.key,"PROVIDER_REFUSED",lambda *_:False)
            self.assertIsNotNone(j.active())

    def test_partial_and_text_only_not_validated(self):
        with Journal(self.temp.name) as j:
            c=self.coordinator(j)
            self.submit(c)
            self.mock.status_result=(200,{"id":TASK,"status":"succeeded","partial":True,
                "media":[{"kind":"video","url":"fixture://video"}]},{})
            self.assertEqual(c.poll(self.key)["state"],"partial")
            with self.assertRaisesRegex(HybridBlocked,"MEDIA_QA_NOT_VERIFIED"):
                c.bind_validated_media(self.key,MEDIA_SHA,lambda *_:True)
            self.mock.status_result=(200,{"id":TASK,"status":"succeeded","partial":False,"media":[]},{})
            self.assertEqual(c.poll(self.key)["state"],"unknown")

    def test_media_and_cam_receipt_separate_gates(self):
        with Journal(self.temp.name) as j:
            c=self.coordinator(j)
            self.assertEqual(self.make_result_ready(c)["state"],"result_ready")
            with self.assertRaisesRegex(HybridBlocked,"MEDIA_QA_NOT_VERIFIED"):
                c.bind_validated_media(self.key,MEDIA_SHA,lambda *_:False)
            self.assertEqual(c.bind_validated_media(self.key,MEDIA_SHA,lambda *_:True)["state"],"awaiting_receipt")
            with self.assertRaisesRegex(HybridBlocked,"CAM_RECEIPT_NOT_VERIFIED"):
                c.confirm_receipt(self.key,"receipt-1",lambda *_:False)
        with Journal(self.temp.name) as j:
            c=self.coordinator(j)
            self.assertEqual(c.poll(self.key)["state"],"awaiting_receipt")
            self.assertEqual(c.confirm_receipt(self.key,"receipt-1",lambda *_:True)["state"],"verified")
            self.assertEqual(c.confirm_receipt(self.key,"receipt-1",lambda *_:False)["state"],"verified")
            self.assertIsNone(j.active())
            self.assertEqual(self.submit(c)["state"],"verified")
        self.assertEqual(self.mock.posts,1)

    def test_journal_never_stores_prompt_png_or_secret(self):
        with Journal(self.temp.name) as j:
            self.submit(self.coordinator(j))
            body=j.path.read_text()
            for prohibited in (PROMPT,"data:image/png","offline-hero-fixture",
                               "Authorization","cookie","lease_token"):
                self.assertNotIn(prohibited,body)

    def test_corrupt_journal_fails_closed(self):
        Path(self.temp.name,"render-intents.json").write_text("{not-json")
        with self.assertRaisesRegex(HybridBlocked,"JOURNAL_REQUIRES_RECONCILIATION"):
            with Journal(self.temp.name):pass

    def test_failed_persistence_never_posts(self):
        with Journal(self.temp.name) as j:
            c=self.coordinator(j)
            with patch("core.os.replace",side_effect=OSError("fixture disk error")):
                with self.assertRaisesRegex(HybridBlocked,"JOURNAL_WRITE_UNCERTAIN"):
                    self.submit(c)
            with self.assertRaisesRegex(HybridBlocked,"JOURNAL_WRITE_UNCERTAIN"):
                self.submit(c)
        self.assertEqual(self.mock.posts,0)


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
    def test_shadow_uses_cam_long_poll_without_claim(self):
        with Journal(self.temp.name) as j:
            calls=[]
            def discover(path):
                calls.append(path)
                return 200,{"items":[ready_intent()]},{}
            d=ContinuousDispatcher(j,discover,lambda _:self.fail("shadow executed"))
            self.assertEqual(d.step(),DispatchResult("SHADOW_CREATE_VIDEO",20))
            self.assertEqual(calls,[DISCOVERY_PATH])
    def test_live_cutover_guard(self):
        with Journal(self.temp.name) as j:
            with self.assertRaisesRegex(HybridBlocked,"LIVE_CUTOVER_NOT_VERIFIED"):
                ContinuousDispatcher(j,None,None,shadow=False)
    def test_cam_no_claim_and_retry_after(self):
        with Journal(self.temp.name) as j:
            d=ContinuousDispatcher(j,lambda _: (200,{"noClaim":True,"pollAfterSeconds":220,
                "items":[ready_intent()]},{}),lambda _:self.fail())
            self.assertEqual(d.step(),DispatchResult("CAM_NOT_READY",220))
            d.discover=lambda _:(429,{},{"Retry-After":"600"})
            self.assertEqual(d.step(),DispatchResult("COOLDOWN",600))
    def test_review_lane_when_renderer_open(self):
        with Journal(self.temp.name) as j:
            d=ContinuousDispatcher(j,lambda _: (200,{"rendererCircuit":{"state":"open"},
                "items":[ready_intent(),ready_intent("review_artwork")]},{}),lambda _:self.fail())
            self.assertEqual(d.step(),DispatchResult("SHADOW_REVIEW_ARTWORK",20))
    def test_unknown_render_blocks_next_discovery(self):
        with Journal(self.temp.name) as j:
            j.prepare(make_intent(),"a"*64)
            d=ContinuousDispatcher(j,lambda _:self.fail("discovery during unresolved render"),None)
            self.assertEqual(d.step(),DispatchResult("RECONCILE_EXISTING_RENDER",20))
    def test_mock_continuous_flow_transitions_after_ack(self):
        with Journal(self.temp.name) as j:
            called=[]
            def execute(item):
                called.append(item["listingId"])
                return DispatchResult("CAM_ACK_CONFIRMED",3)
            d=ContinuousDispatcher(j,lambda _: (200,{"items":[ready_intent()]},{}),
                execute,shadow=False,reconcile=lambda *_:self.fail('unexpected reconcile'),
                authorized_exclusive_runner=True,renderer_verified=True)
            self.assertEqual(d.step(),DispatchResult("CAM_ACK_CONFIRMED",3))
            self.assertEqual(called,["123456789"])
            d.execute=lambda _:DispatchResult("UNKNOWN",3)
            with self.assertRaisesRegex(HybridBlocked,"EXECUTOR_OUTCOME_REQUIRES_RECONCILIATION"):
                d.step()

    def test_resume_before_discovery_after_restart(self):
        mock=MockGateway()
        intent=make_intent()
        with Journal(self.temp.name) as j:
            c=Coordinator(j,GatewayAdapter(mock.request,model="mock-video",
                          compatibility_verified=True),lambda _:True)
            c.submit(intent,original_png=ORIGINAL,hero_png=HERO,
                     hero_artwork_sha=intent.identity.artwork_sha256,prompt=PROMPT)
        with Journal(self.temp.name) as j:
            c=Coordinator(j,GatewayAdapter(mock.request,model="mock-video",
                          compatibility_verified=True),lambda _:True)
            mock.status_result=(200,{"id":TASK,"status":"succeeded","partial":False,
                "media":[{"kind":"video","url":"fixture://video"}]}, {})
            def recover(key,record):
                self.assertEqual(record["task_id"],TASK)
                self.assertEqual(c.poll(key)["state"],"result_ready")
                c.bind_validated_media(key,MEDIA_SHA,lambda *_:True)
                c.confirm_receipt(key,"mock-receipt",lambda *_:True)
                return DispatchResult("CAM_ACK_CONFIRMED",3)
            d=ContinuousDispatcher(j,lambda _:self.fail("discovery before recovery"),
                lambda _:self.fail("unexpected submit"),shadow=False,reconcile=recover,
                authorized_exclusive_runner=True,renderer_verified=True)
            self.assertEqual(d.step(),DispatchResult("CAM_ACK_CONFIRMED",3))
            self.assertIsNone(j.active())
            self.assertEqual(mock.posts,1)


if __name__=="__main__":
    unittest.main()