"""Muse command eligibility regression tests (SQLite, no production state)."""
from datetime import datetime, timedelta, timezone
from unittest import TestCase

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.muse_bridge.command_queue import claimable_commands_query
from app.modules.muse_bridge.model import CamMuseCommand


class MuseCommandQueueTest(TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        # Isolate the command queue rather than creating unrelated tenant tables.
        with self.engine.begin() as conn:
            conn.exec_driver_sql("""
                CREATE TABLE cam_muse_commands (
                    tenant_id VARCHAR(255) NOT NULL,
                    command_id VARCHAR(36) NOT NULL,
                    listing_key VARCHAR(150),
                    action VARCHAR(32) NOT NULL,
                    request_id VARCHAR(128) NOT NULL,
                    status VARCHAR(24) NOT NULL,
                    phase VARCHAR(32) NOT NULL,
                    artwork_sha256 VARCHAR(64),
                    lease_hash VARCHAR(64),
                    lease_expires_at DATETIME,
                    attempts INTEGER NOT NULL,
                    details JSON,
                    submitted_at DATETIME,
                    finished_at DATETIME,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    PRIMARY KEY (tenant_id, command_id)
                )
            """)
        self.now = datetime(2026, 11, 22, tzinfo=timezone.utc)

    def tearDown(self):
        self.engine.dispose()

    def put(self, number, *, tenant="a", status="pending", action="create_video",
            lease=None, attempts=0, created=None):
        with Session(self.engine) as session:
            session.add(CamMuseCommand(
                tenant_id=tenant,
                command_id=f"{number:036d}",
                listing_key=f"{number:010d}",
                action=action,
                request_id=f"req-{tenant}-{number}",
                status=status,
                phase=action,
                attempts=attempts,
                lease_expires_at=lease,
                created_at=created or self.now - timedelta(minutes=5),
                updated_at=self.now,
            ))
            session.commit()

    def fetch(self, tenant="a", *, actions=None, limit=10):
        with Session(self.engine) as session:
            query = claimable_commands_query(tenant, self.now, actions=actions)
            return [row.command_id for row in session.scalars(query.limit(limit)).all()]

    def test_active_leases_do_not_starve_pending_beyond_old_limit(self):
        for i in range(120):
            self.put(i, status="claimed", lease=self.now + timedelta(minutes=20),
                     created=self.now - timedelta(days=1))
        self.put(200, created=self.now)
        self.assertEqual(self.fetch(limit=1), [f"{200:036d}"])

    def test_expired_lease_reclaimable_but_null_lease_fails_closed(self):
        self.put(1, status="claimed", lease=self.now - timedelta(seconds=1))
        self.put(2, status="claimed", lease=None)
        self.put(3, status="claimed", lease=self.now + timedelta(seconds=1))
        self.assertEqual(self.fetch(), [f"{1:036d}"])

    def test_tenant_and_retry_cap_are_enforced(self):
        self.put(1, tenant="other")
        self.put(2, attempts=5)
        self.put(3, status="completed")
        self.put(4, status="pending")
        self.assertEqual(self.fetch(), [f"{4:036d}"])

    def test_action_filter_and_stable_tie_break(self):
        self.put(3, action="execute_task")
        self.put(2, action="review_artwork")
        self.put(1, action="create_video")
        self.assertEqual(self.fetch(actions=("create_video", "review_artwork")),
                         [f"{1:036d}", f"{2:036d}"])
