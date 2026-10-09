"""Verbose Codex workflows stream safely without buffering huge outputs."""
from __future__ import annotations

import asyncio
import json

from app.providers.ai.codex_execution_log import (
    CodexExecutionLog, read_codex_execution_log, stream_codex_process,
    prune_codex_execution_logs,
)


def test_large_stream_uses_bounded_capture_and_redacted_progress(tmp_path):
    class HugeReader:
        def __init__(self, pieces):
            self.pieces = iter(pieces)

        async def read(self, amount):
            try:
                return next(self.pieces)
            except StopIteration:
                return b""

    chunks = [
        b'{"type":"thread.started","thread_id":"thread-safe"}\n',
        *(b"private-output-do-not-log-" * 1200 for _ in range(250)),
        b'{"type":"item.completed","item":{"type":"tool_call","text":"SECRET_BODY"}}\n',
    ]
    class Process:
        returncode = 0
        stdout = HugeReader(chunks)
        stderr = HugeReader([b"warning: " + b"s" * 100_000])

        async def wait(self):
            return self.returncode

    async def run():
        log = CodexExecutionLog(str(tmp_path), "processing-job-1234")
        out, err = await stream_codex_process(Process(), timeout_seconds=30, log=log)
        return out, err

    stdout, stderr = asyncio.run(run())
    assert len(stdout.encode()) < 140_000
    assert len(stderr.encode()) < 140_000
    assert "thread-safe" in stdout
    result = read_codex_execution_log(str(tmp_path), "processing-job-1234")
    assert result is not None
    assert result["state"] == "cli_finished"
    assert result["stdout_bytes"] > 1_000_000
    assert result["stderr_bytes"] > 65_536
    assert result["event_count"] >= 1
    dumped = json.dumps(result)
    assert "private-output-do-not-log" not in dumped
    assert "SECRET_BODY" not in dumped
    assert "tool_call" in dumped


def test_progress_logs_fail_closed_on_untrusted_execution_id(tmp_path):
    log = CodexExecutionLog(str(tmp_path), "../../other-tenant")
    assert not log.enabled
    assert read_codex_execution_log(str(tmp_path), "../../other-tenant") is None


def test_retention_removes_expired_event_and_status_files(tmp_path):
    import os
    import time
    log = CodexExecutionLog(str(tmp_path), "processing-job-old")
    log.close("cli_finished")
    folder = tmp_path / "codex-execution-logs"
    expired = time.time() - 6 * 86400
    for path in folder.iterdir():
        os.utime(path, (expired, expired))
    prune_codex_execution_logs(str(tmp_path))
    assert list(folder.iterdir()) == []
