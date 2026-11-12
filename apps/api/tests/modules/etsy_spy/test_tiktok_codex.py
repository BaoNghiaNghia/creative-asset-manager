"""Codex CLI subprocess contract; tests never call external model/network."""
import asyncio
import fcntl
import os

import pytest

from app.modules.etsy_spy.tiktok_codex import (
    CodexListingUnavailable, generate_codex_listing,
)


def _fake_cli(tmp_path, *, fail=False):
    binary = tmp_path / "fake-codex"
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "args = sys.argv[1:]\n"
        "assert args[0] == 'exec' and '--ephemeral' in args\n"
        "assert '--sandbox' in args and args[args.index('--sandbox') + 1] == 'read-only'\n"
        "assert '--image' in args and os.path.isfile(args[args.index('--image') + 1])\n"
        "assert os.environ.get('OPENAI_API_KEY') is None\n"
        + ("sys.stderr.write('not logged in'); sys.exit(1)\n" if fail else
           "result = {'title': 'Sample Title', 'description': 'Sample description', "
           "'highlights': ['A','B','C','D','E'], "
           "'search_terms': ['one','two','three','four','five','six']}\n"
           "with open(args[args.index('--output-last-message') + 1], 'w') as f:\n"
           "    json.dump(result, f)\n")
        , encoding="utf8",
    )
    binary.chmod(0o700)
    return str(binary)


def _invoke(tmp_path, binary):
    return asyncio.run(generate_codex_listing(
        photo=b"fake-jpeg-content-for-tests",
        mime="image/jpeg",
        prompt="Use the listing skill",
        schema={"type": "object", "properties": {"title": {"type": "string"}}},
        codex_home=str(tmp_path),
        timeout_seconds=45,
        binary=binary,
    ))


def test_cli_runs_ensuring_image_schema_sandbox_and_no_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "SHOULD_NOT_LEAK")
    result = _invoke(tmp_path, _fake_cli(tmp_path))
    assert result["title"] == "Sample Title"
    assert len(result["highlights"]) == 5


def test_cli_quota_auth_failure_is_sanitized(tmp_path):
    with pytest.raises(CodexListingUnavailable, match="tiktok_codex_auth_wait"):
        _invoke(tmp_path, _fake_cli(tmp_path, fail=True))


def test_global_lock_prevents_parallel_cli_execution(tmp_path):
    lock_path = tmp_path / ".tiktok-listing-codex.lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(CodexListingUnavailable, match="tiktok_codex_busy"):
            _invoke(tmp_path, _fake_cli(tmp_path))
    finally:
        os.close(fd)
