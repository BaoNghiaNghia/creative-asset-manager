from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import Base
from app.modules.ai_governance.gemini_quota import GeminiProjectQuotaRepository


class GeminiProjectQuotaRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        database = Path(self.directory.name) / "gemini-quota.db"
        self.engine = create_engine(
            f"sqlite:///{database}",
            connect_args={"check_same_thread": False, "timeout": 5},
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, class_=Session, expire_on_commit=False)
        self.now = datetime(2040, 1, 1, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.directory.cleanup()

    def reserve(
        self, scope: str, model: str, rpd: int, project_rpd: int | None = None
    ):
        with self.sessions() as session:
            result = GeminiProjectQuotaRepository(session).reserve_request(
                quota_scope=scope,
                model=model,
                rpd=rpd,
                project_rpd=project_rpd,
                now=self.now,
            )
            session.commit()
            return result

    def test_reservation_is_shared_across_tenants_for_one_google_project(self) -> None:
        self.assertTrue(self.reserve("creative-assets", "gemini-3.5-flash-lite", 2).allowed)
        self.assertTrue(self.reserve("creative-assets", "gemini-3.5-flash-lite", 2).allowed)
        blocked = self.reserve("creative-assets", "gemini-3.5-flash-lite", 2)
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.reason, "rpd_exhausted")
        self.assertGreater(blocked.available_at, self.now)

    def test_models_and_project_scopes_do_not_share_capacity(self) -> None:
        self.assertTrue(self.reserve("project-a", "gemini-a", 1).allowed)
        self.assertTrue(self.reserve("project-b", "gemini-a", 1).allowed)
        self.assertTrue(self.reserve("project-a", "gemini-b", 1).allowed)

    def test_project_cap_is_shared_across_models(self) -> None:
        self.assertTrue(self.reserve("creative-assets", "gemini-a", 10, 2).allowed)
        self.assertTrue(self.reserve("creative-assets", "gemini-b", 10, 2).allowed)
        blocked = self.reserve("creative-assets", "gemini-c", 10, 2)
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.reason, "project_rpd_exhausted")
        self.assertGreater(blocked.available_at, self.now)

    def test_project_cap_includes_reservations_made_before_global_cap_deploys(self) -> None:
        self.assertTrue(self.reserve("creative-assets", "gemini-a", 10).allowed)
        self.assertTrue(self.reserve("creative-assets", "gemini-b", 10).allowed)
        blocked = self.reserve("creative-assets", "gemini-c", 10, 2)
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.reason, "project_rpd_exhausted")

    def test_model_rejection_does_not_consume_project_capacity(self) -> None:
        self.assertTrue(self.reserve("creative-assets", "gemini-a", 1, 2).allowed)
        self.assertFalse(self.reserve("creative-assets", "gemini-a", 1, 2).allowed)
        self.assertTrue(self.reserve("creative-assets", "gemini-b", 10, 2).allowed)

    def test_concurrent_workers_cannot_reserve_more_than_rpd(self) -> None:
        barrier = threading.Barrier(3)
        outcomes = []
        errors = []

        def reserve() -> None:
            try:
                barrier.wait()
                outcomes.append(self.reserve("creative-assets", "gemini-3.5-flash-lite", 1).allowed)
            except Exception as exc:
                errors.append(exc)

        workers = [threading.Thread(target=reserve) for _ in range(3)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertEqual(errors, [])
        self.assertEqual(outcomes.count(True), 1)
        self.assertEqual(outcomes.count(False), 2)


if __name__ == "__main__":
    unittest.main()
