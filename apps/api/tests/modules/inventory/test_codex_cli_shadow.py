"""No-network tests for the Codex CLI Inventory shadow-analysis boundary."""
from __future__ import annotations

from datetime import date
import json
import os
from pathlib import Path
from types import SimpleNamespace
import pytest

from app.modules.inventory.ai import codex_cli_shadow as shadow


def evidence():
    return {
        "business_date": "2026-11-18",
        "cells": [
            {"sheet": "Kho A", "cell": "C8", "raw_value": "2 hộp",
             "evidence_hash": "a" * 64},
            {"sheet": "Kho A", "cell": "D8", "raw_value": 10,
             "evidence_hash": "b" * 64},
        ],
    }


def review():
    return {
        "summary": "Cần đối chiếu đơn vị tồn.",
        "findings": [{
            "code": "UNIT_REVIEW",
            "message": "Đơn vị chưa được xác minh.",
            "evidence": [{"sheet": "Kho A", "cell": "C8", "evidence_hash": "a" * 64}],
            "confidence": 0.91,
        }],
        "unresolved": ["Chưa biết hệ số quy đổi."],
    }


def test_exact_whitelist_drops_unknown_keys_and_forbids_duplicate_evidence():
    value = evidence()
    value["DATABASE_URL"] = "should-not-leak"
    value["cells"][0]["extra"] = "should-not-leak"
    safe, allowed = shadow._clean_evidence(value)
    assert set(safe) == {"business_date", "cells"}
    assert set(safe["cells"][0]) == {"sheet", "cell", "raw_value", "evidence_hash"}
    assert ("Kho A", "C8", "a"*64) in allowed
    value["cells"].append(dict(value["cells"][0]))
    with pytest.raises(ValueError, match="inventory_codex_duplicate_evidence"):
        shadow._clean_evidence(value)


@pytest.mark.parametrize("mutation", [
    {"business_date": "2026-02-30"},
    {"business_date": "2026-1-18"},
    {"cells": []},
    {"cells": [{"sheet": "Kho A", "cell": "A0",
                "evidence_hash": "a"*64, "raw_value": "2"}]},
    {"cells": [{"sheet": "Kho A", "cell": "A1",
                "evidence_hash": "oops", "raw_value": "2"}]},
])
def test_rejects_invalid_inputs(mutation):
    with pytest.raises(ValueError):
        shadow._clean_evidence({**evidence(), **mutation})


def test_output_cannot_claim_approval_or_invent_citations():
    _, allowed = shadow._clean_evidence(evidence())
    assert shadow._validate_review(review(), allowed) == review()
    forged = review()
    forged["findings"][0]["evidence"][0]["evidence_hash"] = "c" * 64
    with pytest.raises(shadow.InventoryCodexUnavailable, match="inventory_codex_unverified_reference"):
        shadow._validate_review(forged, allowed)
    approved = {**review(), "writes": 100}
    with pytest.raises(shadow.InventoryCodexUnavailable, match="inventory_codex_invalid_output"):
        shadow._validate_review(approved, allowed)


def test_root_identity_fails_closed_before_running_any_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(shadow.os, "geteuid", lambda: 0)
    binary = tmp_path / "codex"
    binary.touch(mode=0o700)
    with pytest.raises(shadow.InventoryCodexUnavailable, match="inventory_codex_service_identity_unavailable"):
        shadow.review_inventory_evidence(evidence(), codex_home=str(tmp_path), binary=str(binary))


def test_cli_uses_readonly_ephemeral_schema_and_no_application_secrets(tmp_path, monkeypatch):
    binary = tmp_path / "codex"
    binary.write_text("")
    binary.chmod(0o700)
    home = tmp_path / "home"
    home.mkdir()
    own_stat = Path.stat

    def stat(self, *args, **kwargs):
        value = own_stat(self, *args, **kwargs)
        if self == home:
            return SimpleNamespace(st_uid=42, st_mode=value.st_mode)
        return value

    monkeypatch.setattr(Path, "stat", stat)
    monkeypatch.setattr(shadow.os, "geteuid", lambda: 42)
    monkeypatch.setenv("DATABASE_URL", "must-not-be-present")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-present")
    received = {}

    class FakeProcess:
        returncode = 0
        pid = 23456

        def __init__(self, command, **kwargs):
            received.update({"command": command, "kwargs": kwargs})
            Path(command[command.index("--output-last-message")+1]).write_text(
                json.dumps(review()), encoding="utf-8"
            )

        def communicate(self, supplied=None, **_):
            received["supplied"] = supplied
            return b"", b""

    monkeypatch.setattr(shadow.subprocess, "Popen", FakeProcess)
    result = shadow.review_inventory_evidence(evidence(), codex_home=str(home), binary=str(binary))
    args = received["command"]
    assert args[0] == str(binary)
    assert args[-1] == "-"
    assert args[args.index("--sandbox")+1] == "read-only"
    assert "--ephemeral" in args and "--output-schema" in args
    assert result["apply_mode"] == "shadow" and result["writes"] == 0
    assert result["verification_status"] == "review_required"
    assert set(received["kwargs"]["env"]).isdisjoint({"DATABASE_URL", "OPENAI_API_KEY", "GOOGLE_API_KEY"})
    assert received["kwargs"]["env"]["CODEX_HOME"] == str(home)
    assert b"should-not-leak" not in received["supplied"]
