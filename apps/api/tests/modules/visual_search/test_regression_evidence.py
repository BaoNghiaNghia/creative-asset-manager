from __future__ import annotations

import subprocess
from pathlib import Path

from app.modules.visual_search.regression_evidence import (
    RegressionCheck,
    run_regression_checks,
)


def test_regression_evidence_requires_every_group_to_pass(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(command, **kwargs):
        calls.append(list(command))
        if any("search-v3" in item for item in command):
            return subprocess.CompletedProcess(
                command,
                1,
                stdout="1 failed in 0.10s\n",
                stderr="",
            )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="3 passed in 0.10s\n",
            stderr="",
        )

    report = run_regression_checks(
        repo_root=tmp_path,
        checks=(
            RegressionCheck("encoder", ("encoder-tests",)),
            RegressionCheck("search_v3", ("search-v3-tests",)),
            RegressionCheck(
                "tenant_isolation",
                ("visual-tests",),
                ("-k", "cross_tenant"),
            ),
        ),
        timeout_seconds=2.0,
        runner=runner,
    )

    assert report["passed"] is False
    assert [row["name"] for row in report["checks"]] == [
        "encoder",
        "search_v3",
        "tenant_isolation",
    ]
    assert report["checks"][0]["passed"] is True
    assert report["checks"][1]["passed"] is False
    assert report["checks"][2]["passed"] is True
    assert report["checks"][2]["pytest_args"] == ["-k", "cross_tenant"]
    assert report["checks"][1]["stdout_summary"] == [
        "1 failed in 0.10s"
    ]
    assert len(calls) == 3
    assert all(call[:3] == [call[0], "-m", "pytest"] for call in calls)
