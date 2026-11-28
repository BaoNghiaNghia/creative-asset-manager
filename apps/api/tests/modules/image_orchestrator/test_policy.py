"""P0 tests use no database, renderer, credentials or real storage."""
import unittest

from app.modules.image_orchestrator import policy as p

ART = "a" * 64
SRC = "b" * 64
PLACEMENT = "c" * 64
NOW = 100000


def pack(listing="123456789", tenant="test-tenant", revision=1, art=ART, garment="C1717"):
    return p.Pack(p.PackIdentity(tenant, listing, art, garment, revision, PLACEMENT, SRC))


def claim(x, worker="muse-01", wave=1, now=NOW, allowed=False, **overrides):
    flags = dict(flow_running=True, capability_verified=True, resource_granted=True)
    flags.update(overrides)
    return p.claim(x, worker, wave, now, wave2_allowed=allowed, **flags)


def start(x, g, role, attempt=None, now=NOW, worker="muse-01", **overrides):
    flags = dict(flow_running=True, resource_granted=True)
    flags.update(overrides)
    return p.role_start(x, worker, g.epoch, g.image_token, now, role,
                        attempt or "attempt-" + str(role), **flags)


def receipt(role, size=1600, **overrides):
    sha = p.digest("image-" + str(role))
    data = dict(image_sha=sha, width=size, height=size, drive_file_id="fake_drive_id",
                qa_image_sha=sha, qa_status="pass", storage_verified=True, placement_sha=PLACEMENT)
    data.update(overrides)
    return p.StoredReceipt(**data)


def finish(x, g, role, size=1600, now=NOW, **overrides):
    return p.verify_role(x, "muse-01", g.epoch, g.image_token, now, role,
                         x.roles[role].attempt_id, receipt(role, size, **overrides))


def fill_wave(x, g, wave, size=1600):
    for i in p.WAVES[wave]:
        start(x, g, i)
        finish(x, g, i, size)


class PolicyTests(unittest.TestCase):
    def test_identity_full_sha_revision_sku_tenant(self):
        original = pack().identity
        self.assertEqual(len(original.pack_id), 64)
        self.assertEqual(original.pack_id, pack().identity.pack_id)
        for other in (pack(revision=2), pack(art="a"*63+"d"),
                      pack(tenant="different"), pack(garment="G500")):
            self.assertNotEqual(original.pack_id, other.identity.pack_id)
        self.assertNotEqual(original.role_key(1), original.role_key(2))
        with self.assertRaisesRegex(p.Conflict, "skill_not_pinned"):
            p.PackIdentity("tenant", "123456", ART, "C1717", 1, PLACEMENT, SRC,
                           p.SUPERSEDED_SKILL_SHA256)

    def test_claim_fail_closed(self):
        for key in ("flow_running", "capability_verified", "resource_granted"):
            with self.assertRaisesRegex(p.Conflict, "image_claim_disabled"):
                claim(pack(), **{key: False})

    def test_one_owner_and_hashed_tokens(self):
        x = pack()
        g = claim(x)
        self.assertNotEqual(g.image_token, g.command_token)
        self.assertNotIn(g.image_token, repr(x))
        self.assertNotIn(g.command_token, repr(g))
        with self.assertRaisesRegex(p.Conflict, "already_claimed"):
            claim(x, "chatgpt-ext-01")
        with self.assertRaisesRegex(p.Conflict, "lease_invalid"):
            p.heartbeat(x, "chatgpt-ext-01", g.epoch, g.command_token, NOW+60)
        with self.assertRaisesRegex(p.Conflict, "lease_invalid"):
            p.heartbeat(x, "muse-01", g.epoch, g.image_token, NOW+60)
        until = p.heartbeat(x, "muse-01", g.epoch, g.command_token, NOW+60)
        self.assertEqual(until, NOW+3660)
        p.authorized(x, "muse-01", g.epoch, g.image_token, NOW+3601)

    def test_start_idempotence_and_pause(self):
        x = pack()
        g = claim(x)
        self.assertTrue(start(x, g, 1))
        self.assertFalse(start(x, g, 1))
        self.assertEqual(x.roles[1].attempts, 1)
        with self.assertRaisesRegex(p.Conflict, "reconciliation_required"):
            start(x, g, 2)
        finish(x, g, 1)
        with self.assertRaisesRegex(p.Conflict, "image_start_disabled"):
            start(x, g, 2, flow_running=False)
        # STOP blocks new sends, not completion of already-started work.
        self.assertEqual(x.roles[1].status, "verified")

    def test_lost_lease_does_not_prove_failure(self):
        x = pack()
        g = claim(x)
        start(x, g, 1)
        self.assertTrue(p.expire(x, NOW+3600))
        self.assertEqual(x.roles[1].status, "reconcile_required")
        with self.assertRaisesRegex(p.Conflict, "reconciliation_required"):
            claim(x, now=NOW+3601)
        with self.assertRaisesRegex(p.Conflict, "unsafe_owner_release"):
            p.release_owner(x, operator_approved=True)
        with self.assertRaisesRegex(p.Conflict, "failure_not_confirmed"):
            p.confirmed_failure(x, 1, "attempt-1", "timeout", terminal=False)
        p.confirmed_failure(x, 1, "attempt-1", "provider_confirmed_no_result",
                            terminal=False, proof_of_no_usable_result=True)
        with self.assertRaisesRegex(p.Conflict, "pack_owner_mismatch"):
            claim(x, "chatgpt-ext-01", now=NOW+3601)
        g2 = claim(x, now=NOW+3601)
        with self.assertRaisesRegex(p.Conflict, "lease_invalid"):
            start(x, g, 1, now=NOW+3601)
        self.assertTrue(start(x, g2, 1, "second", now=NOW+3601))

    def test_exact_retry_cap_and_attempt_id_reuse(self):
        x = pack()
        g = claim(x)
        for n in range(3):
            aid = f"try-{n}"
            self.assertTrue(start(x, g, 1, aid))
            p.confirmed_failure(x, 1, aid, "verified_failure", terminal=False,
                                proof_of_no_usable_result=True)
        self.assertEqual(x.roles[1].status, "hold")
        self.assertEqual(x.roles[1].attempts, 3)
        with self.assertRaisesRegex(p.Conflict, "role_not_pending"):
            start(x, g, 1, "extra")
        with self.assertRaisesRegex(p.Conflict, "attempt_id_reused"):
            start(x, g, 2, "try-0")

    def test_receipt_requires_qa_bound_sha_drive_and_square(self):
        x = pack()
        g = claim(x)
        start(x, g, 1)
        for changes in ({"storage_verified": False}, {"qa_status":"unverified"},
                        {"qa_image_sha": "d"*64}, {"placement_sha":"d"*64},
                        {"drive_file_id":""}):
            with self.assertRaisesRegex(p.Conflict, "invalid_verified_receipt"):
                finish(x, g, 1, **changes)
        with self.assertRaisesRegex(p.Conflict, "invalid_native_dimensions"):
            finish(x, g, 1, height=2000)
        with self.assertRaisesRegex(p.Conflict, "invalid_native_dimensions"):
            finish(x, g, 1, size=1024)
        self.assertEqual(finish(x, g, 1), "verified")
        self.assertEqual(finish(x, g, 1), "already_verified")
        self.assertFalse(x.roles[1].receipt.publication_ready)
        with self.assertRaisesRegex(p.Conflict, "immutable_role_receipt"):
            finish(x, g, 1, size=2000)

    def test_global_barrier_snapshot_and_timeout_isolation(self):
        a,b = pack(),pack("123456780")
        g=claim(a); fill_wave(a,g,1)
        p.complete(a,"muse-01",g.epoch,g.command_token,NOW,"success")
        bgrant=claim(b)
        start(b,bgrant,1)
        batch=p.Batch("pilot","test-tenant",(a.identity.pack_id,b.identity.pack_id),NOW)
        self.assertFalse(p.barrier(batch,[a,b],NOW)["open"])
        with self.assertRaisesRegex(p.Conflict,"wave_barrier_closed"):
            claim(a,wave=2)
        res=p.barrier(batch,[a,b],NOW+p.BARRIER_SECONDS)
        self.assertTrue(res["open"])
        self.assertEqual(res["wave2_eligible"],[a.identity.pack_id])
        self.assertEqual(res["isolated"],[b.identity.pack_id])
        self.assertIsNotNone(b.lease)
        self.assertTrue(b.uncertain)
        with self.assertRaisesRegex(p.Conflict,"batch_scope_mismatch"):
            p.barrier(batch,[a,pack("123456780",tenant="foreign")],NOW)
        with self.assertRaisesRegex(p.Conflict,"batch_scope_mismatch"):
            p.barrier(batch,[a,b,pack("123456781")],NOW)

    def test_partial_ack_and_resume_same_owner(self):
        x=pack(); g=claim(x)
        start(x,g,1); finish(x,g,1)
        with self.assertRaisesRegex(p.Conflict,"outcome_receipt_mismatch"):
            p.complete(x,"muse-01",g.epoch,g.command_token,NOW,"success")
        res=p.complete(x,"muse-01",g.epoch,g.command_token,NOW,"partial")
        self.assertEqual(res["missingRoles"],[2,3,4])
        g2=claim(x,now=NOW+1)
        self.assertTrue(p.complete(x,"muse-01",g.epoch,g.command_token,NOW+2,"partial")["ackReplayed"])
        self.assertEqual(x.lease.epoch,g2.epoch)
        with self.assertRaisesRegex(p.Conflict,"role_not_pending"):
            start(x,g2,1,"new-attempt",now=NOW+2)
        for i in (2,3,4):
            start(x,g2,i,now=NOW+2); finish(x,g2,i,now=NOW+2)
        p.complete(x,"muse-01",g2.epoch,g2.command_token,NOW+2,"success")
        self.assertTrue(x.wave_complete(1))
        self.assertTrue(p.complete(x,"muse-01",g.epoch,g.command_token,NOW+3,"partial")["ackReplayed"])

    def test_adaptive_claim_budget_keeps_no_progress_limit(self):
        x=pack()
        for n in range(p.MAX_WAVE_CLAIMS-1):
            g=claim(x,now=NOW+n)
            r=p.complete(x,"muse-01",g.epoch,g.command_token,NOW+n,"failed")
            self.assertFalse(r["productiveSlice"])
        g=claim(x,now=NOW+5)
        start(x,g,1,now=NOW+5)
        finish(x,g,1,now=NOW+5)
        r=p.complete(x,"muse-01",g.epoch,g.command_token,NOW+5,"partial")
        self.assertTrue(r["productiveSlice"])
        self.assertEqual(r["remainingNoProgressClaims"],1)
        p.complete(x,"muse-01",g.epoch,g.command_token,NOW+5,"partial")
        self.assertEqual(x.wave_claims[1],p.MAX_WAVE_CLAIMS-1)
        g=claim(x,now=NOW+6)
        r=p.complete(x,"muse-01",g.epoch,g.command_token,NOW+6,"partial")
        self.assertFalse(r["productiveSlice"])
        with self.assertRaisesRegex(p.Conflict,"wave_claim_limit"):
            claim(x,now=NOW+7)

    def test_historical_lease_without_progress_baseline_does_not_refund(self):
        x=pack()
        g=claim(x)
        x.lease.verified_at_claim=None
        start(x,g,1)
        finish(x,g,1)
        r=p.complete(x,"muse-01",g.epoch,g.command_token,NOW,"partial")
        self.assertFalse(r["productiveSlice"])
        self.assertEqual(x.wave_claims[1],1)

    def test_no_completion_with_ambiguous_generation(self):
        x=pack();g=claim(x);start(x,g,1)
        with self.assertRaisesRegex(p.Conflict,"reconciliation_required"):
            p.complete(x,"muse-01",g.epoch,g.command_token,NOW,"failed")

    def test_infographic_requires_approved_inputs(self):
        x=pack(); g=claim(x);fill_wave(x,g,1)
        p.complete(x,"muse-01",g.epoch,g.command_token,NOW,"success")
        g2=claim(x,wave=2,allowed=True)
        with self.assertRaisesRegex(p.Conflict,"infographic_dependencies_missing"):
            start(x,g2,13)
        fill_wave(x,g2,2)
        p.complete(x,"muse-01",g2.epoch,g2.command_token,NOW,"success")
        self.assertTrue(x.complete)
        self.assertFalse(x.publishable)

    def test_native_2000_pack_is_publishable_only_after_all_roles(self):
        x=pack();g=claim(x);fill_wave(x,g,1,2000)
        p.complete(x,"muse-01",g.epoch,g.command_token,NOW,"success")
        self.assertFalse(x.publishable)
        g2=claim(x,wave=2,allowed=True);fill_wave(x,g2,2,2000)
        p.complete(x,"muse-01",g2.epoch,g2.command_token,NOW,"success")
        self.assertTrue(x.publishable)
