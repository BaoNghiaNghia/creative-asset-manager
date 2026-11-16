"""YTrends Picked V5: stream source-backed writes without losing checkpoints."""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

from app.modules.etsy_spy.ytrends_mcp import empty_evidence

SCRIPT = (Path(__file__).resolve().parents[5] / "scripts"
          / "cam-ytrends-refresh-all-picked.py")


def load_script():
    spec = importlib.util.spec_from_file_location(
        "cam_ytrends_full_picked_checkpoint", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class FakeDB:
    def __init__(self, jobs):
        self.jobs = jobs
        self.jobs_by_id = {job.id: job for job in jobs}
        self.read_number = 0
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def scalars(self, _statement):
        self.read_number += 1
        rows = ([job.entity_id for job in self.jobs]
                if self.read_number == 1 else self.jobs)
        return SimpleNamespace(all=lambda: rows)

    def scalar(self, statement):
        # No SQL is executed: resolve the row-lock query's job id param.
        for value in statement.compile().params.values():
            if value in self.jobs_by_id:
                return self.jobs_by_id[value]
        raise AssertionError("Row lock did not target an existing job")

    def commit(self):
        self.commits += 1


def setup(monkeypatch, *, count=9):
    script = load_script()
    jobs = [
        SimpleNamespace(
            id=f"job-{i}", entity_id=f"10000000{i}", status="completed",
            cancellation_requested=False, created_at=None,
            payload_json={
                "source_title": f"theme {i}",
                "result": {"title": f"draft {i}", "warnings": []},
            },
        )
        for i in range(count)
    ]
    db = FakeDB(jobs)
    monkeypatch.setattr(script, "SessionLocal", lambda: db)
    monkeypatch.setattr(script, "get_settings", lambda: SimpleNamespace(
        ETSY_TIKTOK_YTRENDS_ENABLED=True,
        ETSY_TIKTOK_YTRENDS_BEARER_TOKEN=SimpleNamespace(
            get_secret_value=lambda: ""),
        ETSY_TIKTOK_YTRENDS_TIMEOUT_SECONDS=8.0,
    ))
    return script, db


def connected(title):
    return {
        "source": "trends.ytuong.ai", "marketplace": "etsy",
        "status": "connected", "query": title, "as_of_date": "2026-11-16",
        "total_listings": 17,
        "keywords": [{"keyword": title, "status": "connected",
                      "listings": 17, "competition": "low",
                      "as_of_date": "2026-11-16"}],
    }


def test_persists_probe_before_later_failing_windows(monkeypatch):
    script, db = setup(monkeypatch)
    later_seen = []

    async def provider(title, **_):
        if title in {"theme 0", "theme 4", "theme 8"}:
            return connected(title)
        later_seen.append(db.commits)
        return empty_evidence(title)

    monkeypatch.setattr(script, "research_etsy_keyword_set", provider)
    out = asyncio.run(script.execute(
        "creative-assets-prod", limit=9, apply=True, concurrency=1))
    assert out["status"] == "provider_degraded_partial"
    assert db.commits == out["updated_latest"] == 3
    assert out["verified_rows_before"] == 0
    assert out["verified_rows_after"] == 3
    assert out["titles_processed"] == out["jobs_processed"] == 9
    assert out["batches_completed"] == 3
    assert later_seen and all(commits == 3 for commits in later_seen)
    for job in db.jobs:
        draft = job.payload_json["result"]
        assert draft["title"] == f"draft {int(job.id.split('-')[1])}"
        if job.id in {"job-0", "job-4", "job-8"}:
            assert draft["ytrends_evidence"]["research_revision"] == script.RESEARCH_REVISION
        else:
            assert "ytrends_evidence" not in draft


def test_no_provider_evidence_means_zero_writes(monkeypatch):
    script, db = setup(monkeypatch, count=3)

    async def failed(title, **_):
        return empty_evidence(title)

    monkeypatch.setattr(script, "research_etsy_keyword_set", failed)
    out = asyncio.run(script.execute(
        "creative-assets-prod", limit=3, apply=True, concurrency=2))
    assert out["status"] == "provider_unavailable_no_writes"
    assert db.commits == 0
    assert out["provider_probe_unverified"] == 3


def test_resume_keeps_stable_pagination_and_skips_verified_v5(monkeypatch):
    script, db = setup(monkeypatch, count=3)
    db.jobs[0].payload_json["result"]["ytrends_evidence"] = {
        **connected("theme 0"),
        "research_revision": script.RESEARCH_REVISION,
    }
    out = asyncio.run(script.execute(
        "creative-assets-prod", limit=2, offset=0,
        apply=False, concurrency=2))
    assert out["eligible_jobs"] == 3
    assert out["selected_complete_jobs"] == 2
    assert out["selected_jobs"] == 1
    assert out["already_verified_latest"] == 1
    assert db.commits == 0
