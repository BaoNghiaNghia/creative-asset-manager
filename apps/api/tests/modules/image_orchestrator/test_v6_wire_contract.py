"""Offline regression against delivered V6 batch-claim fixture."""
import copy
import unittest

from app.modules.image_orchestrator import policy as p
from app.modules.image_orchestrator import v6_wire_contract as v

def fixture():
    return {"protocol":v.PROTOCOL,"batchId":f"{5:032x}","listingId":"4603662026","wave":1,
        "calls":[{"callId":f"{r:032x}","roles":[r],"outputMapping":{"0":r},
            "methods":{str(r):"native_imagegen"},"attemptIds":{str(r):f"{r+5:032x}"}}
            for r in range(1,5)]}

def validate(m, sha=None, wave=1, **kwargs):
    return v.validate_manifest(m, sha or v.manifest_sha256(m),
        listing_id="4603662026", wave=wave, **kwargs)

class WireContractTests(unittest.TestCase):
    def test_exact_muse_fixture_sha_and_mapping(self):
        m=fixture()
        self.assertEqual(v.manifest_sha256(m),"7d7bf94edee711a550a41fb569aa1acfcc13b5b447efedc5a3bdccba30485caa")
        self.assertEqual([x[1] for x in validate(m)], [1,2,3,4])
    def test_tampered_sha(self):
        with self.assertRaises(p.Conflict): validate(fixture(), "a"*64)
    def test_wrong_wave(self):
        m=fixture(); m["wave"]=2
        with self.assertRaises(p.Conflict): validate(m,wave=2)
    def test_wrong_listing(self):
        m=fixture(); m["listingId"]="11111"
        with self.assertRaises(p.Conflict): validate(m)
    def test_duplicate_call(self):
        m=fixture(); m["calls"][1]["callId"]=m["calls"][0]["callId"]
        with self.assertRaises(p.Conflict): validate(m)
    def test_duplicate_attempt(self):
        m=fixture(); m["calls"][1]["attemptIds"]["2"]=m["calls"][0]["attemptIds"]["1"]
        with self.assertRaises(p.Conflict): validate(m)
    def test_duplicate_role(self):
        m=fixture(); m["calls"][1]=copy.deepcopy(m["calls"][0]); m["calls"][1]["callId"]="a"*32
        with self.assertRaises(p.Conflict): validate(m)
    def test_unattested_multi_output(self):
        m=fixture(); m["calls"][0]["roles"]=[1,2]
        with self.assertRaises(p.Conflict): validate(m)
    def test_bad_output_mapping(self):
        m=fixture(); m["calls"][0]["outputMapping"]={"0":2}
        with self.assertRaises(p.Conflict): validate(m)
    def test_hybrid_requires_trusted_authorization(self):
        m=fixture(); m["calls"][0]["methods"]["1"]="hybrid_compositor"
        with self.assertRaises(p.Conflict): validate(m)
        self.assertEqual(validate(m,hybrid_role1_authorized=True)[0][3],"hybrid_compositor")
    def test_hybrid_wrong_role(self):
        m=fixture(); m["calls"][1]["methods"]["2"]="hybrid_compositor"
        with self.assertRaises(p.Conflict): validate(m,hybrid_role1_authorized=True)
    def test_infographic_gate(self):
        with self.assertRaises(p.Conflict): v.require_infographic_dependencies(13,[1,5,6])
        v.require_infographic_dependencies(13,[1,5,6,7])
    def test_candidate_remains_disabled(self):
        r=v.candidate_registry()
        self.assertFalse(r["claimEnabled"])
        self.assertFalse(r["rendererAllowed"])
        self.assertFalse(r["multiOutputSupported"])
        self.assertFalse(r["autoWaveBarrier"])
        self.assertEqual(r["publicationMinimumPx"],2000)
