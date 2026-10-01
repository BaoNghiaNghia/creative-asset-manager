from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.error_archive import (
    append_archived_errors,
    classify_error_message,
    iter_archived_errors,
    normalize_journal_error,
    purge_expired_archives,
    redact_error_text,
)


def test_structured_error_is_captured() -> None:
    captured, severity = classify_error_message(
        '{"level":"ERROR","event":"job_failed","logger":"cam.worker"}'
    )
    assert captured is True
    assert severity == "error"


def test_info_with_non_success_error_code_is_captured() -> None:
    captured, _severity = classify_error_message(
        '{"level":"INFO","event":"job_finished","error_code":"visual_index_source_unavailable"}'
    )
    assert captured is True


def test_normal_info_is_ignored() -> None:
    captured, _severity = classify_error_message(
        '{"level":"INFO","event":"job_finished","error_code":"none"}'
    )
    assert captured is False


def test_deferred_failure_is_archived_as_warning() -> None:
    captured, severity = classify_error_message(
        '{"level":"INFO","event":"job_finished","error_code":"managed_storage_staging_capacity_reached","final_outcome":"deferred"}'
    )
    assert captured is True
    assert severity == "warning"


def test_harmless_warning_failed_message_is_ignored() -> None:
    captured, _severity = classify_error_message(
        "WARNING:root:Failed to create openvino_telemetry file."
    )
    assert captured is False


def test_benign_systemd_lifecycle_for_error_named_unit_is_ignored() -> None:
    for message in (
        "Starting creative-asset-manager-error-logger.service - Creative Asset Manager 10-day error log collector...",
        "Finished creative-asset-manager-error-logger.service - Creative Asset Manager 10-day error log collector.",
        "creative-asset-manager-error-logger.service: Deactivated successfully.",
        "creative-asset-manager-error-logger.service: Consumed 1.2s CPU time.",
    ):
        assert classify_error_message(message)[0] is False


def test_systemd_failure_is_still_captured() -> None:
    captured, severity = classify_error_message(
        "creative-asset-manager-api.service: Failed with result 'timeout'."
    )
    assert captured is True
    assert severity == "error"


def test_error_named_worker_lifecycle_is_not_a_false_positive() -> None:
    captured, _severity = classify_error_message(
        "Started creative-asset-manager-error-logger.timer - Collect Creative Asset Manager errors every 5 minutes."
    )
    assert captured is False


def test_plain_traceback_and_critical_are_captured() -> None:
    assert classify_error_message("Traceback (most recent call last):")[0] is True
    assert classify_error_message("CRITICAL: worker crashed") == (True, "critical")


def test_redaction_removes_tokens_and_url_queries() -> None:
    value = redact_error_text(
        "Authorization: Bearer super-secret-token-123 "
        "https://example.test/path?access_token=abc#frag "
        'api_key:"topsecret"'
    )
    assert "super-secret-token-123" not in value
    assert "access_token=abc" not in value
    assert "topsecret" not in value
    assert "https://example.test/path" in value


def test_normalize_resolves_systemd_manager_unit_and_redacts() -> None:
    record = {
        "UNIT": "creative-asset-manager-api.service",
        "_SYSTEMD_UNIT": "init.scope",
        "MESSAGE": "ERROR: request failed https://example.test/api?token=secret",
        "__CURSOR": "cursor-1",
        "__REALTIME_TIMESTAMP": "1760000000000000",
        "_PID": "123",
    }
    row = normalize_journal_error(record)
    assert row is not None
    assert row["service"] == "creative-asset-manager-api.service"
    assert row["journal_cursor"] == "cursor-1"
    assert "?token=secret" not in row["message"]


def test_archive_retention_and_read_window(tmp_path: Path) -> None:
    now = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    recent = now - timedelta(days=2)
    just_inside = now - timedelta(days=10) + timedelta(minutes=1)
    just_outside = now - timedelta(days=10) - timedelta(minutes=1)
    old = now - timedelta(days=11)
    append_archived_errors(
        tmp_path,
        [
            {
                "timestamp": recent.isoformat(),
                "severity": "error",
                "service": "creative-asset-manager-api.service",
                "message": "recent",
            },
            {
                "timestamp": just_inside.isoformat(),
                "severity": "error",
                "service": "creative-asset-manager-api.service",
                "message": "inside",
            },
            {
                "timestamp": just_outside.isoformat(),
                "severity": "error",
                "service": "creative-asset-manager-api.service",
                "message": "outside",
            },
            {
                "timestamp": old.isoformat(),
                "severity": "error",
                "service": "creative-asset-manager-api.service",
                "message": "old",
            },
        ],
    )
    assert purge_expired_archives(tmp_path, now=now) == 1
    rows = list(iter_archived_errors(tmp_path, since=now - timedelta(days=10), until=now))
    assert [row["message"] for row in rows] == ["inside", "recent"]
