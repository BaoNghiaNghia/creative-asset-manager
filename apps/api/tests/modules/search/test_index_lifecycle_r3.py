import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.core.database import Base
from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV3Index
from app.modules.search.governance_model import SearchIndexRecordModel
from app.modules.search.index_lifecycle import IndexVerificationError, SearchIndexLifecycleService, VerificationSpec
from app.modules.search.index_types import AliasSwitchResult


class FakeLifecycleProvider:
    def __init__(self):
        self.definition = ElasticsearchV3Index.index_definition()
        self.aliases = {"read": set(), "write": set()}
        self.count = 10
        self.mismatched_projection_count = 0
        self.fixture_hits = [{"_id": "asset-a", "_source": {"asset_id": "asset-a", "tenant_id": "tenant-a"}}, {"_id": "asset-b", "_source": {"asset_id": "asset-b", "tenant_id": "tenant-a"}}]
        self.deleted = []
        self.fail_after_switch = False
        self.alias_checks = 0
        self.alias_sequence = []
        self.leak_tenant = False
        self.search_bodies = []

    async def index_mapping(self, name):
        return {name: {"mappings": self.definition["mappings"]}}

    async def index_settings(self, name):
        return {name: {"settings": {"index": self.definition["settings"]}}}

    async def index_count(self, _name):
        return self.count

    async def verification_search(self, _name, body):
        self.search_bodies.append(body)
        query = body.get("query", {})
        if "must_not" in query.get("bool", {}):
            return {"hits": {"total": {"value": self.mismatched_projection_count}, "hits": []}}
        filters = query.get("bool", {}).get("filter", [])
        tenant = next((item["term"]["tenant_id"] for item in filters if "term" in item and "tenant_id" in item["term"]), None)
        hits = self.fixture_hits
        if tenant and not self.leak_tenant:
            hits = [hit for hit in hits if hit["_source"]["tenant_id"] == tenant]
        return {"hits": {"total": {"value": len(hits)}, "hits": hits}}

    async def alias_indices(self):
        self.alias_checks += 1
        if self.alias_sequence:
            value = self.alias_sequence.pop(0)
            return {key: set(indices) for key, indices in value.items()}
        return {key: set(value) for key, value in self.aliases.items()}

    async def switch_aliases(self, target):
        previous = tuple(sorted(self.aliases["read"]))
        self.aliases = {"read": {target}, "write": {target}}
        if self.fail_after_switch:
            self.fail_after_switch = False
            raise RuntimeError("interrupted after atomic alias update")
        return AliasSwitchResult(target, previous, previous)

    async def delete_index(self, name):
        self.deleted.append(name)


class ElasticsearchLifecycleRemediationTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.provider = FakeLifecycleProvider()
        self.service = SearchIndexLifecycleService(self.session, self.provider)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    async def verified(self, name="cam-v2-new"):
        row = self.service.register(
            physical_index_name=name, index_prefix="cam",
            index_version=name.rsplit("-", 1)[-1], projection_version="projection-v2",
        )
        await self.service.verify(
            row.id,
            VerificationSpec(
                "projection-v2", minimum_document_count=9,
                expected_document_count=11, document_count_tolerance=1,
                required_queries=({
                    "_name": "cat ranking", "_expected_asset_ids": ["asset-a", "asset-b"],
                    "_expected_ranking": True, "query": {"match": {"search_text": "cat"}},
                },),
                tenant_ids=("tenant-a",),
            ),
            actor_id="admin",
        )
        return row

    async def test_verification_has_explicit_verified_state_and_complete_evidence(self):
        row = await self.verified()
        self.assertEqual(row.lifecycle_state, "verified")
        self.assertTrue(row.verification_json["dynamic_strict"])
        self.assertTrue(row.verification_json["mapping_matches"])
        self.assertTrue(row.verification_json["analyzer_matches"])
        self.assertTrue(row.verification_json["projection_version_documents_match"])
        self.assertTrue(row.verification_json["document_count_within_tolerance"])
        self.assertTrue(row.verification_json["fixtures"][0]["passed"])
        self.assertTrue(all(not key.startswith("_") for body in self.provider.search_bodies for key in body))
        self.assertTrue(row.verification_json["tenant_isolation"][0]["passed"])

        self.provider.mismatched_projection_count = 1
        with self.assertRaises(IndexVerificationError):
            await self.service.verify(row.id, VerificationSpec("projection-v2"), actor_id="admin")
        self.assertEqual(row.lifecycle_state, "failed")

    async def test_v3_verification_requires_scope_mapping_and_normalized_filename(self):
        definition = deepcopy(ElasticsearchV3Index.index_definition())
        analysis = definition["settings"]["analysis"]
        analysis["normalizer"] = {
            "cam_normalized": {"type": "custom", "filter": ["lowercase", "asciifolding"]}
        }
        properties = definition["mappings"]["properties"]
        properties.update({
            "source_id": {"type": "keyword"},
            "parent_id": {"type": "keyword"},
            "ancestor_ids": {"type": "keyword"},
            "visible_text": {"type": "text", "analyzer": "cam_text_v2"},
            "search_suggest": {"type": "search_as_you_type", "analyzer": "cam_text_v2"},
            "filename": {
                "type": "text", "analyzer": "cam_text_v2",
                "fields": {"normalized": {"type": "keyword", "normalizer": "cam_normalized"}},
            },
        })
        self.provider.definition = definition
        row = self.service.register(
            physical_index_name="cam-v3-verified", index_prefix="cam-v3",
            index_version="verified", projection_version="projection-v3",
        )
        await self.service.verify(
            row.id, VerificationSpec("projection-v3", tenant_ids=("tenant-a",)),
            actor_id="admin",
        )
        self.assertTrue(row.verification_json["mapping_fields"]["source_id"])
        self.assertTrue(row.verification_json["mapping_fields"]["ancestor_ids"])
        self.assertTrue(row.verification_json["mapping_fields"]["filename.normalized"])

        self.provider.definition["mappings"]["properties"].pop("ancestor_ids")
        rejected = self.service.register(
            physical_index_name="cam-v3-incomplete", index_prefix="cam-v3",
            index_version="incomplete", projection_version="projection-v3",
        )
        with self.assertRaises(IndexVerificationError):
            await self.service.verify(
                rejected.id, VerificationSpec("projection-v3", tenant_ids=("tenant-a",)),
                actor_id="admin",
            )
        self.assertFalse(rejected.verification_json["mapping_fields"]["ancestor_ids"])
    async def test_interrupted_activation_reconciles_and_previous_rolls_back(self):
        old = self.service.register(
            physical_index_name="cam-v2-old", index_prefix="cam",
            index_version="old", projection_version="projection-v1",
        )
        old.lifecycle_state = "active"
        old.activated_at = datetime.now(timezone.utc) - timedelta(days=2)
        self.provider.aliases = {"read": {old.physical_index_name}, "write": {old.physical_index_name}}
        new = await self.verified()
        self.provider.fail_after_switch = True
        with self.assertRaises(RuntimeError):
            await self.service.activate(new.id, actor_id="admin")
        self.assertEqual(new.lifecycle_state, "activating")
        recovered = await self.service.reconcile_aliases("cam", actor_id="admin")
        self.assertEqual(recovered.id, new.id)
        self.assertEqual(new.lifecycle_state, "active")
        self.assertEqual(old.lifecycle_state, "previous")

        rolled = await self.service.rollback(old.id, actor_id="admin")
        self.assertEqual(rolled.id, old.id)
        self.assertEqual(old.lifecycle_state, "active")
        self.assertEqual(new.lifecycle_state, "previous")

    async def test_cleanup_is_bounded_confirmed_resumable_and_alias_safe(self):
        active = self.service.register(
            physical_index_name="cam-v2-active", index_prefix="cam",
            index_version="active", projection_version="v2",
        )
        active.lifecycle_state = "active"
        previous = self.service.register(
            physical_index_name="cam-v2-previous", index_prefix="cam",
            index_version="previous", projection_version="v1",
        )
        previous.lifecycle_state = "previous"
        retired = []
        for suffix in ("one", "two"):
            row = self.service.register(
                physical_index_name=f"cam-v2-{suffix}", index_prefix="cam",
                index_version=suffix, projection_version="v0",
            )
            row.lifecycle_state = "retired"
            row.retired_at = datetime.now(timezone.utc) - timedelta(days=5)
            retired.append(row)
        self.provider.aliases = {"read": {active.physical_index_name}, "write": {active.physical_index_name}}
        dry = await self.service.cleanup(
            index_prefix="cam", actor_id="admin", min_age=timedelta(days=1),
            preserve_previous=1, limit=1, dry_run=True,
        )
        self.assertEqual(len(dry), 1)
        with self.assertRaises(ValueError):
            await self.service.cleanup(
                index_prefix="cam", actor_id="admin", min_age=timedelta(days=1),
                preserve_previous=1, dry_run=False,
            )
        cancelled = await self.service.cleanup(
            index_prefix="cam", actor_id="admin", min_age=timedelta(days=1),
            preserve_previous=1, dry_run=False, confirmed=True,
            cancellation_requested=lambda: True,
        )
        self.assertEqual(cancelled, [])
        deleted = await self.service.cleanup(
            index_prefix="cam", actor_id="admin", min_age=timedelta(days=1),
            preserve_previous=1, limit=1, dry_run=False, confirmed=True,
        )
        self.assertEqual(len(deleted), 1)
        self.assertEqual(self.provider.deleted, deleted)
        self.assertEqual(active.lifecycle_state, "active")
        self.assertEqual(previous.lifecycle_state, "previous")
        self.assertGreaterEqual(self.provider.alias_checks, 2)

    async def test_verification_rejects_incomplete_definition_failures_and_tenant_leak(self):
        row = self.service.register(
            physical_index_name="cam-v2-invalid", index_prefix="cam",
            index_version="invalid", projection_version="projection-v2",
        )
        self.provider.definition["mappings"]["properties"]["path_values"]["properties"].pop("value")
        with self.assertRaises(IndexVerificationError):
            await self.service.verify(row.id, VerificationSpec("projection-v2"), actor_id="admin")
        self.assertFalse(row.verification_json["mapping_fields"]["path_values.value"])

        self.provider = FakeLifecycleProvider()
        self.service = SearchIndexLifecycleService(self.session, self.provider)
        row = self.service.register(
            physical_index_name="cam-v2-bad-analyzer", index_prefix="cam-analyzer",
            index_version="analyzer", projection_version="projection-v2",
        )
        self.provider.definition["settings"]["analysis"]["analyzer"]["cam_text_v2"]["tokenizer"] = "whitespace"
        with self.assertRaises(IndexVerificationError):
            await self.service.verify(row.id, VerificationSpec("projection-v2"), actor_id="admin")
        self.assertFalse(row.verification_json["analyzer_matches"])

        self.provider = FakeLifecycleProvider()
        self.service = SearchIndexLifecycleService(self.session, self.provider)
        row = self.service.register(
            physical_index_name="cam-v2-leaky", index_prefix="cam-leaky",
            index_version="leaky", projection_version="projection-v2",
        )
        self.provider.fixture_hits.append({
            "_id": "asset-cross", "_source": {"asset_id": "asset-cross", "tenant_id": "tenant-b"},
        })
        self.provider.leak_tenant = True
        with self.assertRaises(IndexVerificationError):
            await self.service.verify(
                row.id, VerificationSpec("projection-v2", tenant_ids=("tenant-a",)),
                actor_id="admin",
            )
        self.assertFalse(row.verification_json["tenant_isolation"][0]["passed"])

    async def test_invalid_transition_divergent_aliases_and_last_second_alias_protection(self):
        building = self.service.register(
            physical_index_name="cam-v2-building", index_prefix="cam",
            index_version="building", projection_version="v2",
        )
        with self.assertRaises(IndexVerificationError):
            await self.service.activate(building.id, actor_id="admin")

        self.provider.aliases = {"read": {"cam-v2-a"}, "write": {"cam-v2-b"}}
        with self.assertRaises(IndexVerificationError):
            await self.service.reconcile_aliases("cam", actor_id="admin")

        active = self.service.register(
            physical_index_name="cam-v2-active-race", index_prefix="race",
            index_version="active", projection_version="v2",
        )
        active.lifecycle_state = "active"
        retired = self.service.register(
            physical_index_name="cam-v2-retired-race", index_prefix="race",
            index_version="retired", projection_version="v1",
        )
        retired.lifecycle_state = "retired"
        retired.retired_at = datetime.now(timezone.utc) - timedelta(days=5)
        safe = {"read": {active.physical_index_name}, "write": {active.physical_index_name}}
        newly_protected = {"read": {retired.physical_index_name}, "write": {retired.physical_index_name}}
        self.provider.alias_sequence = [safe, newly_protected]
        deleted = await self.service.cleanup(
            index_prefix="race", actor_id="admin", min_age=timedelta(days=1),
            preserve_previous=1, limit=1, dry_run=False, confirmed=True,
        )
        self.assertEqual(deleted, [])
        self.assertEqual(self.provider.deleted, [])
        self.assertEqual(retired.lifecycle_state, "retired")

if __name__ == "__main__":
    unittest.main()
