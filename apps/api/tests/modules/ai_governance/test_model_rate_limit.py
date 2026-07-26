from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import Base
from app.modules.ai_governance.rate_limit import AiModelRateLimitRepository


class AiModelRateLimitRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        database = Path(self.directory.name) / "rate-limit.db"
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

    def reserve(self, model: str, rpm: int):
        with self.sessions() as session:
            result = AiModelRateLimitRepository(session).reserve_start(
                tenant_id="tenant-a",
                provider="gemini",
                model=model,
                rpm=rpm,
                minimum_interval_seconds=10,
                now=self.now,
            )
            session.commit()
            return result

    def test_uses_model_specific_rpm_and_ten_second_minimum(self) -> None:
        slow = self.reserve("gemini-2.5-flash", 3)
        fast = self.reserve("gemini-3.5-flash-lite", 20)
        self.assertTrue(slow.allowed)
        self.assertEqual(slow.delay_seconds, 20)
        self.assertTrue(fast.allowed)
        self.assertEqual(fast.delay_seconds, 10)
        self.assertFalse(self.reserve("gemini-2.5-flash", 3).allowed)
        self.assertFalse(self.reserve("gemini-3.5-flash-lite", 20).allowed)

    def test_concurrent_workers_reserve_only_one_start_slot(self) -> None:
        barrier = threading.Barrier(2)
        decisions = []
        errors = []

        def reserve(worker: str) -> None:
            try:
                barrier.wait()
                with self.sessions() as session:
                    decision = AiModelRateLimitRepository(session).reserve_start(
                        tenant_id="tenant-a",
                        provider="gemini",
                        model="gemini-2.5-flash",
                        rpm=5,
                        minimum_interval_seconds=10,
                        now=self.now,
                    )
                    session.commit()
                    decisions.append((worker, decision.allowed))
            except Exception as exc:  # pragma: no cover - assertion below reports it
                errors.append(exc)

        threads = [threading.Thread(target=reserve, args=(name,)) for name in ("a", "b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(sum(allowed for _, allowed in decisions), 1)
        self.assertEqual(sum(not allowed for _, allowed in decisions), 1)


if __name__ == "__main__":
    unittest.main()
