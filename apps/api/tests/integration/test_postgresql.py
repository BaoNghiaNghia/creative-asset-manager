from __future__ import annotations

import os
import threading
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, func, inspect, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.health import postgresql_is_ready
from app.core.database import (
    init_database,
    validate_alembic_head,
    validate_database_connection,
)
from app.modules.assets.model import (
    AssetModel,
    AssetSourceLinkModel,
    ExternalSourceModel,
    SourceAssetModel,
)
from app.modules.auth_persistence.login import ApplicationLoginService
from app.modules.auth_persistence.model import (
    AuthAuditEventModel,
    TenantMembershipModel,
    UserIdentityModel,
    UserModel,
)
from app.modules.assets.repository import (
    AssetContentConflictError,
    AssetRegistryRepository,
)
from app.modules.auth_persistence.tenant_membership import TenantMembershipService
from app.modules.authorization.model import MembershipRoleModel, RoleModel
from app.modules.authorization.seed import seed_tenant_rbac
from app.modules.authorization.service import TenantAuthorizationService
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.processing.service import ProcessingJobService
from app.modules.pipeline.model import AssetPipelineModel
from app.modules.tag.model import TagModel
from app.operations.tag_cli import seed_system_tags


DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL") or os.getenv("DATABASE_URL", "")
POSTGRES_AVAILABLE = DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg://"))


@unittest.skipUnless(POSTGRES_AVAILABLE, "PostgreSQL integration database is not configured")
class PostgreSqlRepositoryIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = create_engine(DATABASE_URL, pool_pre_ping=True)
        cls.sessions = sessionmaker(cls.engine, class_=Session, expire_on_commit=False)
        if cls.engine.dialect.name != "postgresql":
            raise RuntimeError("PostgreSQL integration tests require PostgreSQL")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()

    def test_migrations_reached_head_on_real_postgresql(self) -> None:
        tables = set(inspect(self.engine).get_table_names())
        self.assertIn("alembic_version", tables)
        self.assertIn("processing_jobs", tables)
        self.assertIn("oauth_connections", tables)
        with self.engine.connect() as connection:
            version = connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one()
        heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
        self.assertEqual(len(heads), 1)
        self.assertEqual(version, heads[0])

    def test_production_health_check_uses_real_postgresql(self) -> None:
        self.assertTrue(postgresql_is_ready())

    def test_production_startup_connection_head_and_idempotent_seed(self) -> None:
        settings = Settings(
            APP_ENV="production",
            PUBLIC_APP_URL="https://assets.example.com",
            CORS_ALLOWED_ORIGINS="https://assets.example.com",
            TRUSTED_HOSTS="api.example.com",
            API_DOCS_ENABLED=False,
            DATABASE_URL=DATABASE_URL,
        )
        validate_database_connection(self.engine)
        self.assertEqual(
            validate_alembic_head(self.engine, database_url=DATABASE_URL),
            ScriptDirectory.from_config(Config("alembic.ini")).get_heads()[0],
        )
        init_database(settings, database_engine=self.engine)

        seed_system_tags(self.sessions)
        seed_system_tags(self.sessions)
        with self.sessions() as session:
            count = session.scalar(
                select(func.count()).select_from(TagModel).where(
                    TagModel.id.in_(("public", "draft"))
                )
            )
        self.assertEqual(count, 2)

    def test_tenant_constraints_and_source_identity(self) -> None:
        marker = uuid4().hex
        content_hash = marker.ljust(64, "0")[:64]
        tenant_a = f"pg-a-{marker}"
        tenant_b = f"pg-b-{marker}"
        with self.sessions() as session:
            repository = AssetRegistryRepository(session)
            repository.create_asset(tenant_id=tenant_a, content_hash=content_hash)
            repository.create_asset(tenant_id=tenant_b, content_hash=content_hash)
            session.commit()

        with self.sessions() as session:
            with self.assertRaises(AssetContentConflictError):
                AssetRegistryRepository(session).create_asset(
                    tenant_id=tenant_a, content_hash=content_hash
                )
            session.rollback()

        with self.sessions() as session:
            repository = AssetRegistryRepository(session)
            first_source = repository.upsert_external_source(
                tenant_id=tenant_a,
                source_key=f"drive-{marker}",
                source_type="google_drive",
            )
            second_source = repository.upsert_external_source(
                tenant_id=tenant_a,
                source_key=f"sharepoint-{marker}",
                source_type="sharepoint",
            )
            first = repository.upsert_source_asset(
                tenant_id=tenant_a,
                external_source_id=first_source.id,
                external_asset_id="same-external-id",
            )
            second = repository.upsert_source_asset(
                tenant_id=tenant_a,
                external_source_id=second_source.id,
                external_asset_id="same-external-id",
            )
            session.commit()
            self.assertNotEqual(first.id, second.id)
            self.assertIsNone(repository.get_source_asset(tenant_b, first.id))
            self.assertEqual(
                session.scalar(
                    select(AssetModel).where(
                        AssetModel.tenant_id == tenant_a,
                        AssetModel.content_hash == content_hash,
                    )
                ).tenant_id,
                tenant_a,
            )

    def test_concurrent_workers_claim_one_job_with_skip_locked(self) -> None:
        marker = uuid4().hex
        with self.sessions() as session:
            job = ProcessingRepository(session).create_job(
                tenant_id=f"pg-claim-{marker}",
                job_type="source_asset_download",
                entity_type="source_asset",
                entity_id=marker,
                idempotency_key=f"pg-claim-{marker}",
            )
            session.commit()
            job_id = job.id

        barrier = threading.Barrier(2)
        claimed: list[str | None] = []
        errors: list[BaseException] = []
        lock = threading.Lock()

        def worker(worker_id: str) -> None:
            try:
                barrier.wait(timeout=5)
                with self.sessions() as session:
                    result = ProcessingJobService(
                        ProcessingRepository(session)
                    ).claim_next(
                        worker_id=worker_id,
                        lease_seconds=30,
                        allowed_job_types=("source_asset_download",),
                    )
                with lock:
                    claimed.append(result.id if result else None)
            except BaseException as exc:
                with lock:
                    errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=("pg-worker-a",)),
            threading.Thread(target=worker, args=("pg-worker-b",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(errors)
        self.assertEqual(claimed.count(job_id), 1)
        self.assertEqual(claimed.count(None), 1)

    def test_expired_lease_is_recovered_without_cross_tenant_access(self) -> None:
        marker = uuid4().hex
        tenant_id = f"pg-lease-{marker}"
        with self.sessions() as session:
            ProcessingRepository(session).create_job(
                tenant_id=tenant_id,
                job_type="source_asset_download",
                entity_type="source_asset",
                entity_id=marker,
                idempotency_key=f"pg-lease-{marker}",
            )
            session.commit()
            now = datetime.now(timezone.utc)
        with self.sessions() as session:
            first = ProcessingJobService(ProcessingRepository(session)).claim_next(
                worker_id="dead-worker",
                lease_seconds=1,
                now=now,
                allowed_job_types=("source_asset_download",),
            )
        with self.sessions() as session:
            before_expiry = ProcessingJobService(
                ProcessingRepository(session)
            ).claim_next(
                worker_id="early-worker",
                lease_seconds=1,
                now=now + timedelta(milliseconds=500),
                allowed_job_types=("source_asset_download",),
            )
        with self.sessions() as session:
            recovered = ProcessingJobService(
                ProcessingRepository(session)
            ).claim_next(
                worker_id="recovery-worker",
                lease_seconds=30,
                now=now + timedelta(seconds=2),
                allowed_job_types=("source_asset_download",),
            )
        self.assertIsNotNone(first)
        self.assertIsNone(before_expiry)
        self.assertEqual(recovered.id, first.id)
        self.assertEqual(recovered.claimed_by, "recovery-worker")
        with self.sessions() as session:
            persisted = session.get(ProcessingJobModel, first.id)
            self.assertEqual(persisted.attempt_count, 2)

    def test_concurrent_tenant_membership_creation_is_idempotent(self) -> None:
        marker = uuid4().hex
        tenant_id = f"pg-membership-{marker}"
        with self.sessions() as session:
            user = UserModel(primary_email=f"{marker}@example.com", status="active")
            session.add(user)
            session.flush()
            user_id = user.id
            TenantMembershipService(session).create_tenant(
                tenant_id=tenant_id,
                name="Concurrent tenant",
                slug=tenant_id,
            )
            session.commit()

        barrier = threading.Barrier(2)
        errors: list[BaseException] = []
        membership_ids: list[str] = []
        lock = threading.Lock()

        def add_membership() -> None:
            try:
                barrier.wait(timeout=5)
                with self.sessions() as session:
                    membership = TenantMembershipService(session).add_member(
                        tenant_id=tenant_id,
                        user_id=user_id,
                    )
                    session.commit()
                    with lock:
                        membership_ids.append(membership.id)
            except BaseException as exc:
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=add_membership) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(errors)
        self.assertEqual(len(set(membership_ids)), 1)
        with self.sessions() as session:
            count = session.scalar(select(func.count()).select_from(TenantMembershipModel).where(
                TenantMembershipModel.tenant_id == tenant_id,
                TenantMembershipModel.user_id == user_id,

            ))
            self.assertEqual(count, 1)

    def test_concurrent_tenant_role_assignment_is_idempotent(self) -> None:
        marker = uuid4().hex
        tenant_id = f"pg-role-{marker}"
        with self.sessions() as session:
            user = UserModel(primary_email=f"role-{marker}@example.com", status="active")
            session.add(user)
            session.flush()
            membership_service = TenantMembershipService(session)
            membership_service.create_tenant(tenant_id=tenant_id, name="Role tenant", slug=tenant_id)
            membership = membership_service.add_member(tenant_id=tenant_id, user_id=user.id)
            seed_tenant_rbac(session, tenant_id)
            role = session.scalar(select(RoleModel).where(
                RoleModel.tenant_id == tenant_id,
                RoleModel.role_key == "viewer",
            ))
            membership_id = membership.id
            role_id = role.id
            session.commit()

        barrier = threading.Barrier(2)
        errors: list[BaseException] = []
        assignment_ids: list[str] = []
        lock = threading.Lock()

        def assign_role() -> None:
            try:
                barrier.wait(timeout=5)
                with self.sessions() as session:
                    assignment = TenantAuthorizationService(session).assign_role(
                        tenant_id=tenant_id,
                        membership_id=membership_id,
                        role_id=role_id,
                        actor_id="integration-test",
                    )
                    session.commit()
                    with lock:
                        assignment_ids.append(assignment.id)
            except BaseException as exc:
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=assign_role) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(errors)
        self.assertEqual(len(set(assignment_ids)), 1)
        with self.sessions() as session:
            count = session.scalar(select(func.count()).select_from(MembershipRoleModel).where(
                MembershipRoleModel.tenant_id == tenant_id,
                MembershipRoleModel.tenant_membership_id == membership_id,
                MembershipRoleModel.role_id == role_id,
            ))
            self.assertEqual(count, 1)

    def test_concurrent_jit_oauth_login_is_atomic_and_idempotent(self) -> None:
        marker = uuid4().hex
        tenant_id = f"pg-jit-{marker}"
        subject = f"google-{marker}"
        with self.sessions() as session:
            memberships = TenantMembershipService(session)
            memberships.create_tenant(
                tenant_id=tenant_id,
                name="JIT tenant",
                slug=tenant_id,
            )
            seed_tenant_rbac(session, tenant_id)
            session.commit()

        settings = Settings(
            AUTH_SELF_SIGNUP_ENABLED=True,
            AUTH_DEFAULT_TENANT_ID=tenant_id,
            AUTH_ALLOWED_EMAIL_DOMAINS="example.com",
            AUTH_SELF_SIGNUP_DEFAULT_ROLE="viewer",
        )
        barrier = threading.Barrier(2)
        errors: list[BaseException] = []
        login_results: list[tuple[str, bool]] = []
        lock = threading.Lock()

        def login() -> None:
            try:
                barrier.wait(timeout=5)
                with self.sessions() as session:
                    result = ApplicationLoginService(session, settings).resolve(
                        provider="google",
                        provider_subject=subject,
                        provider_email=f"{marker}@example.com",
                        display_name="Concurrent user",
                    )
                    session.commit()
                    with lock:
                        login_results.append((result.user.id, result.first_login))
            except BaseException as exc:
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=login) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        self.assertFalse(errors)
        self.assertEqual(len(login_results), 2)
        self.assertEqual(len({item[0] for item in login_results}), 1)
        self.assertEqual(sorted(item[1] for item in login_results), [False, True])

        with self.sessions() as session:
            identity = session.scalar(
                select(UserIdentityModel).where(
                    UserIdentityModel.provider == "google",
                    UserIdentityModel.provider_subject == subject,
                )
            )
            membership = session.scalar(
                select(TenantMembershipModel).where(
                    TenantMembershipModel.tenant_id == tenant_id,
                    TenantMembershipModel.user_id == identity.user_id,
                )
            )
            assignments = session.scalar(
                select(func.count())
                .select_from(MembershipRoleModel)
                .join(RoleModel, RoleModel.id == MembershipRoleModel.role_id)
                .where(
                    MembershipRoleModel.tenant_membership_id == membership.id,
                    RoleModel.role_key == "viewer",
                )
            )
            creation_events = session.scalar(
                select(func.count())
                .select_from(AuthAuditEventModel)
                .where(
                    AuthAuditEventModel.tenant_id == tenant_id,
                    AuthAuditEventModel.action
                    == "self_signup_default_role_assigned",
                )
            )
            self.assertIsNotNone(identity)
            self.assertEqual(membership.status, "active")
            self.assertEqual(assignments, 1)
            self.assertEqual(creation_events, 1)


    def test_asset_pipeline_detaches_optional_references_without_losing_history(self) -> None:
        marker = uuid4().hex
        tenant_id = f"pg-pipeline-detach-{marker}"
        created_at = datetime.now(timezone.utc)
        snapshot = {
            "correlation_id": f"correlation-{marker}",
            "origin_type": "source_asset",
            "origin_id": f"origin-{marker}",
            "analysis_id": marker,
            "state": "completed",
            "content_hash": marker.ljust(64, "0")[:64],
            "projection_version": "v-test",
            "status_data_json": {"preserved": True, "marker": marker},
            "created_at": created_at,
            "completed_at": created_at,
        }
        with self.sessions() as session:
            source = ExternalSourceModel(
                tenant_id=tenant_id,
                source_key=f"source-{marker}",
                source_type="google_drive",
            )
            asset = AssetModel(tenant_id=tenant_id, content_hash=snapshot["content_hash"])
            session.add_all((source, asset))
            session.flush()
            source_asset = SourceAssetModel(
                tenant_id=tenant_id,
                external_source_id=source.id,
                external_asset_id=f"file-{marker}",
            )
            session.add(source_asset)
            session.flush()
            session.add(AssetSourceLinkModel(
                tenant_id=tenant_id,
                asset_id=asset.id,
                source_asset_id=source_asset.id,
            ))
            pipeline = AssetPipelineModel(
                tenant_id=tenant_id,
                source_asset_id=source_asset.id,
                asset_id=asset.id,
                **snapshot,
            )
            session.add(pipeline)
            session.commit()
            pipeline_id, source_asset_id, asset_id = pipeline.id, source_asset.id, asset.id

        with self.sessions() as session:
            session.delete(session.get(SourceAssetModel, source_asset_id))
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()

        with self.sessions() as session:
            session.delete(session.get(AssetModel, asset_id))
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()

        with self.sessions() as session:
            session.execute(
                update(AssetPipelineModel)
                .where(
                    AssetPipelineModel.tenant_id == tenant_id,
                    AssetPipelineModel.source_asset_id == source_asset_id,
                )
                .values(source_asset_id=None)
            )
            session.delete(session.get(SourceAssetModel, source_asset_id))
            session.commit()

        with self.sessions() as session:
            detached = session.get(AssetPipelineModel, pipeline_id)
            self.assertIsNotNone(detached)
            self.assertEqual(detached.tenant_id, tenant_id)
            self.assertIsNone(detached.source_asset_id)
            self.assertEqual(detached.asset_id, asset_id)
            for field, expected in snapshot.items():
                self.assertEqual(getattr(detached, field), expected)

        with self.sessions() as session:
            session.execute(
                update(AssetPipelineModel)
                .where(
                    AssetPipelineModel.tenant_id == tenant_id,
                    AssetPipelineModel.asset_id == asset_id,
                )
                .values(asset_id=None)
            )
            session.delete(session.get(AssetModel, asset_id))
            session.commit()

        with self.sessions() as session:
            detached = session.get(AssetPipelineModel, pipeline_id)
            self.assertIsNotNone(detached)
            self.assertEqual(detached.tenant_id, tenant_id)
            self.assertIsNone(detached.source_asset_id)
            self.assertIsNone(detached.asset_id)
            self.assertEqual(detached.origin_id, snapshot["origin_id"])
            self.assertEqual(detached.state, snapshot["state"])
            self.assertEqual(detached.status_data_json, snapshot["status_data_json"])

    def test_asset_pipeline_composite_foreign_keys_preserve_tenant_scope(self) -> None:
        marker = uuid4().hex
        tenant_a = f"pg-pipeline-a-{marker}"
        tenant_b = f"pg-pipeline-b-{marker}"
        with self.sessions() as session:
            source = ExternalSourceModel(
                tenant_id=tenant_b,
                source_key=f"source-{marker}",
                source_type="google_drive",
            )
            asset = AssetModel(tenant_id=tenant_b, content_hash=marker.ljust(64, "0")[:64])
            session.add_all((source, asset))
            session.flush()
            source_asset = SourceAssetModel(
                tenant_id=tenant_b,
                external_source_id=source.id,
                external_asset_id=f"file-{marker}",
            )
            session.add(source_asset)
            session.commit()

        with self.sessions() as session:
            invalid = AssetPipelineModel(
                tenant_id=tenant_a,
                correlation_id=f"cross-tenant-{marker}",
                origin_type="source_asset",
                origin_id=source_asset.id,
                source_asset_id=source_asset.id,
                asset_id=asset.id,
                state="completed",
                status_data_json={},
            )
            session.add(invalid)
            with self.assertRaises(IntegrityError):
                session.flush()
            session.rollback()

        with self.engine.connect() as connection:
            definitions = dict(connection.execute(text("""
                SELECT conname, pg_get_constraintdef(oid)
                FROM pg_constraint
                WHERE conname IN (
                    'fk_asset_pipelines_source_asset',
                    'fk_asset_pipelines_asset'
                )
            """)).all())
        # PostgreSQL omits its default NO ACTION clause from pg_get_constraintdef.
        # The raw-delete assertions above prove the fail-closed behavior.
        self.assertNotIn("ON DELETE SET NULL", definitions[
            "fk_asset_pipelines_source_asset"
        ])
        self.assertNotIn("ON DELETE SET NULL", definitions[
            "fk_asset_pipelines_asset"
        ])

if __name__ == "__main__":
    unittest.main()
