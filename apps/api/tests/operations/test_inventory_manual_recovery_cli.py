from datetime import date, datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import Mock, patch

_ROOT = Path(__file__).resolve().parents[4]
_SPEC = spec_from_file_location(
    "inventory_scheduler_main",
    _ROOT / "apps" / "inventory_scheduler" / "main.py",
)
assert _SPEC and _SPEC.loader
_MODULE = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
_run_manual_recovery_current_day = _MODULE._run_manual_recovery_current_day


def test_replay_queue_cli_is_exclusive():
    import pytest
    with pytest.raises(SystemExit):
        _MODULE._arguments(["--replay-queue", "--once"])
    with pytest.raises(SystemExit):
        _MODULE._arguments(["--replay-queue", "--manual-recovery-current-day"])


def test_replay_queue_oneshot_runs_without_creating_lifecycle_scheduler(capsys):
    scheduler = Mock()
    queue = Mock()
    queue.run_once.return_value = 1
    queue.enqueue_failed_evening_days.return_value = 2
    queue.last_auto_scan = {"v4_tenants": 1, "eligible": 2}
    queue.recent_failure_reasons.return_value = []
    queue.recent_replay_results.return_value = []
    settings = Mock(inventory_tenant_allowlist=frozenset({"tenant-a"}))
    with patch.object(_MODULE, "get_settings", return_value=settings), patch.object(
        _MODULE, "HistoricalReplayQueue", return_value=queue
    ), patch.object(_MODULE, "InventoryDailyScheduler", return_value=scheduler):
        assert _MODULE.main(["--replay-queue"]) == 0
    scheduler.assert_not_called()
    queue.enqueue_failed_evening_days.assert_called_once_with(frozenset({"tenant-a"}))
    queue.run_once.assert_called_once_with(frozenset({"tenant-a"}))
    assert "INVENTORY_REPLAY_QUEUE_ONESHOT auto_enqueued=2 executions=1" in capsys.readouterr().out


def test_replay_timer_emits_only_change_activity_or_six_hour_heartbeat(tmp_path, capsys):
    queue = Mock(last_auto_scan={
        "v4_tenants": 1, "snapshots": 7, "verified": 4,
        "slot_not_retryable": 1, "already_requested": 1,
    })
    queue.recent_failure_reasons.return_value = [
        {"business_date": "2026-10-13", "error_code": "inventory_gemini_request_failed",
         "reason": "inventory_gemini_request_failed"},
    ]
    queue.recent_replay_results.return_value = [
        {"business_date": "2026-10-13", "status": "review_required",
         "verification_status": "not_executed", "promoted": False},
    ]
    state_file = tmp_path / "replay-audit-state.json"
    allowlist = frozenset({"tenant-a"})
    now = datetime(2030, 8, 10, 8, tzinfo=timezone.utc)
    def tick(*, minutes=0, enqueued=0, executed=0):
        from datetime import timedelta
        return _MODULE._emit_replay_audit(
            queue, allowed_tenant_ids=allowlist,
            auto_enqueued=enqueued, executions=executed,
            now=now + timedelta(minutes=minutes), state_file=state_file,
        )

    assert tick() is True
    first = capsys.readouterr().out
    assert "INVENTORY_REPLAY_ATTENTION review_jobs=1 blocked_candidates=2" in first
    assert "INVENTORY_REPLAY_RESULTS" in first
    state = state_file.read_text()
    assert "inventory_gemini_request_failed" not in state
    assert "tenant-a" not in state
    assert tick(minutes=1) is False
    assert capsys.readouterr().out == ""

    queue.last_auto_scan = {**queue.last_auto_scan, "eligible": 1}
    assert tick(minutes=2) is True
    assert "reason=changed" in capsys.readouterr().out
    assert tick(minutes=3) is False
    assert capsys.readouterr().out == ""
    assert tick(minutes=362) is True
    assert "reason=heartbeat" in capsys.readouterr().out
    assert tick(minutes=363, executed=1) is True
    assert "reason=activity" in capsys.readouterr().out
    queue.recent_replay_results.assert_called_with(allowlist)
    queue.recent_failure_reasons.assert_called_with(allowlist)


def test_replay_timer_corrupt_or_unwritable_digest_state_never_hides_errors(tmp_path, capsys):
    queue = Mock(last_auto_scan={"v4_tenants": 1})
    queue.recent_failure_reasons.return_value = [{"reason": "inventory_gemini_request_failed"}]
    queue.recent_replay_results.return_value = []
    now = datetime(2030, 8, 10, 8, tzinfo=timezone.utc)
    state = tmp_path / "replay-audit-state.json"
    state.write_text('{"fingerprint": "broken", "last_emitted_at": "not-a-date"}')
    args = dict(
        queue=queue, allowed_tenant_ids=frozenset({"tenant-a"}),
        auto_enqueued=0, executions=0, now=now,
    )
    assert _MODULE._emit_replay_audit(**args, state_file=state) is True
    assert "INVENTORY_REPLAY_DIAGNOSTICS" in capsys.readouterr().out
    assert _MODULE._emit_replay_audit(
        **args, state_file=tmp_path / "missing-subdirectory" / "state.json",
    ) is True
    assert "INVENTORY_REPLAY_DIAGNOSTICS" in capsys.readouterr().out


def test_replay_diagnostics_is_read_only_and_never_processes_jobs(capsys):
    queue = Mock()
    queue.recent_failure_reasons.return_value = [
        {"business_date": "2026-10-14", "error_code": "inventory_gemini_transport_error", "reason": "transient"}
    ]
    queue.recent_replay_results.return_value = [
        {"business_date": "2026-10-14", "status": "review_required", "verification_status": "not_verified", "promoted": False}
    ]
    settings = Mock(inventory_tenant_allowlist=frozenset({"tenant-a"}))
    with patch.object(_MODULE, "get_settings", return_value=settings), patch.object(
        _MODULE, "HistoricalReplayQueue", return_value=queue
    ), patch.object(_MODULE, "InventoryDailyScheduler") as scheduler:
        assert _MODULE.main(["--replay-diagnostics"]) == 0
    queue.recent_failure_reasons.assert_called_once_with(frozenset({"tenant-a"}))
    queue.recent_replay_results.assert_called_once_with(frozenset({"tenant-a"}))
    queue.enqueue_failed_evening_days.assert_not_called()
    queue.run_once.assert_not_called()
    scheduler.assert_not_called()
    output = capsys.readouterr().out
    assert "INVENTORY_REPLAY_DIAGNOSTICS" in output
    assert "INVENTORY_REPLAY_RESULTS" in output
    assert "2026-10-14" in output


def test_replay_diagnostics_rejects_mutating_cli_combinations():
    import pytest
    for argv in (
        ["--replay-diagnostics", "--replay-queue"],
        ["--replay-diagnostics", "--once"],
        ["--replay-diagnostics", "--manual-recovery-current-day"],
    ):
        with pytest.raises(SystemExit):
            _MODULE._arguments(argv)


def test_diagnostic_date_inspection_is_strictly_read_only(capsys):
    requested = ("2026-10-08", "2026-10-13", "2026-10-14", "2026-10-15")
    queue = Mock()
    queue.recent_failure_reasons.return_value = []
    queue.recent_replay_results.return_value = []
    queue.inspect_dates.return_value = [{
        "business_date": "2026-10-08",
        "profiles": [{"apply_mode": "shadow", "replay_status": "review_required"}],
    }]
    settings = Mock(inventory_tenant_allowlist=frozenset({"tenant-a"}))
    with patch.object(_MODULE, "get_settings", return_value=settings), patch.object(
        _MODULE, "HistoricalReplayQueue", return_value=queue
    ), patch.object(_MODULE, "InventoryDailyScheduler") as scheduler:
        assert _MODULE.main(["--replay-diagnostics", "--inspect-dates", ",".join(requested)]) == 0
    queue.inspect_dates.assert_called_once_with(
        frozenset({"tenant-a"}), tuple(date.fromisoformat(day) for day in requested),
    )
    queue.enqueue_failed_evening_days.assert_not_called()
    queue.run_once.assert_not_called()
    scheduler.assert_not_called()
    assert "INVENTORY_DATE_INSPECTION" in capsys.readouterr().out


def test_date_inspection_fails_closed_without_accessible_v4_tenant(capsys):
    queue = Mock()
    queue.recent_failure_reasons.return_value = []
    queue.recent_replay_results.return_value = []
    queue.inspect_dates.return_value = [
        {"business_date": "2026-10-08", "profiles": []},
        {"business_date": "2026-10-13", "profiles": []},
    ]
    settings = Mock(inventory_tenant_allowlist=frozenset())
    with patch.object(_MODULE, "get_settings", return_value=settings), patch.object(
        _MODULE, "HistoricalReplayQueue", return_value=queue
    ), patch.object(_MODULE, "InventoryDailyScheduler") as scheduler:
        result = _MODULE.main([
            "--replay-diagnostics",
            "--inspect-dates", "2026-10-08,2026-10-13",
        ])
    assert result == 2
    output = capsys.readouterr().out
    assert "INVENTORY_DATE_INSPECTION status=unverified" in output
    assert "reason=no_accessible_v4_tenants" in output
    assert "status=evidence_available" not in output
    scheduler.assert_not_called()
    queue.enqueue_failed_evening_days.assert_not_called()
    queue.run_once.assert_not_called()


def test_diagnostic_date_list_is_validated_without_service_access():
    import pytest
    for argv in (
        ["--inspect-dates", "2026-10-08"],
        ["--replay-queue", "--inspect-dates", "2026-10-08"],
        ["--replay-diagnostics", "--inspect-dates", "2026-10-08,2026-10-08"],
        ["--replay-diagnostics", "--inspect-dates", "2026-02-30"],
        ["--replay-diagnostics", "--inspect-dates", "2026-10-08,"],
        ["--replay-diagnostics", "--inspect-dates", ",".join(f"2026-10-{i:02d}" for i in range(1, 10))],
    ):
        with pytest.raises(SystemExit):
            _MODULE._arguments(argv)


def _preview():
    return {
        "status": "preview_ready",
        "business_date": "2030-08-10",
        "plan_hash": "a" * 64,
        "safe_operation_count": 2,
        "write_operation_count": 1,
        "excluded_clear_count": 3,
        "operations": [
            {
                "sheet": "WH1",
                "cell": "B2",
                "source_sheet": "WH1",
                "source_cell": "H2",
                "needs_write": True,
            },
            {
                "sheet": "WH2",
                "cell": "B2",
                "source_sheet": "WH2",
                "source_cell": "H2",
                "needs_write": False,
            },
        ],
    }


def test_manual_recovery_one_shot_noops_without_candidate(capsys):
    scheduler = Mock()
    with patch.object(
        _MODULE,
        "_manual_recovery_scan",
        return_value=([], {
            "v4_tenants": 1,
            "carry_absent": 0,
            "carry_completed": 1,
            "carry_eligible": 0,
            "other_errors": {},
        }),
    ):
        result = _run_manual_recovery_current_day(
            scheduler,
            frozenset({"tenant-a"}),
            now=datetime(2030, 8, 10, 7, tzinfo=timezone.utc),
        )

    assert result == 0
    assert "status=noop" in capsys.readouterr().out
    scheduler.preview_v4_morning_reset_recovery.assert_not_called()
    scheduler.apply_v4_morning_reset_recovery.assert_not_called()


def test_manual_recovery_retryable_gemini_failure_previews_then_applies(capsys):
    assert "inventory_gemini_rate_limited" in _MODULE._MANUAL_RECOVERY_ERROR_CODES
    assert "inventory_gemini_transport_error" in _MODULE._MANUAL_RECOVERY_ERROR_CODES
    scheduler = Mock()
    scheduler.preview_v4_morning_reset_recovery.return_value = _preview()
    scheduler.apply_v4_morning_reset_recovery.return_value = {
        "status": "completed",
        "business_date": "2030-08-10",
        "plan_hash": "a" * 64,
        "applied_count": 1,
        "already_correct_count": 1,
        "excluded_clear_count": 3,
    }
    now = datetime(2030, 8, 10, 7, tzinfo=timezone.utc)
    with patch.object(
        _MODULE,
        "_manual_recovery_scan",
        return_value=([(
            "tenant-a",
            date(2030, 8, 10),
            "inventory_gemini_rate_limited",
        )], {
            "v4_tenants": 1,
            "carry_absent": 0,
            "carry_completed": 0,
            "carry_eligible": 1,
            "other_errors": {},
            "plan_issue_codes": {"MISSING_CATALOG_ITEMS": 1},
        }),
    ):
        result = _run_manual_recovery_current_day(
            scheduler,
            frozenset({"tenant-a"}),
            now=now,
        )

    assert result == 0
    scheduler.preview_v4_morning_reset_recovery.assert_called_once_with(
        "tenant-a", date(2030, 8, 10), now
    )
    scheduler.apply_v4_morning_reset_recovery.assert_called_once_with(
        "tenant-a",
        date(2030, 8, 10),
        plan_hash="a" * 64,
        now=now,
    )
    output = capsys.readouterr().out
    assert "status=preview_ready" in output
    assert "status=completed" in output


def test_manual_recovery_one_shot_blocks_ambiguous_candidates(capsys):
    scheduler = Mock()
    with patch.object(
        _MODULE,
        "_manual_recovery_scan",
        return_value=([
            ("tenant-a", date(2030, 8, 10), "previous_day_gemini_not_verified"),
            ("tenant-b", date(2030, 8, 10), "previous_day_gemini_not_verified"),
        ], {
            "v4_tenants": 2,
            "carry_absent": 0,
            "carry_completed": 0,
            "carry_eligible": 2,
            "other_errors": {},
        }),
    ):
        result = _run_manual_recovery_current_day(
            scheduler,
            frozenset({"tenant-a", "tenant-b"}),
            now=datetime(2030, 8, 10, 7, tzinfo=timezone.utc),
        )

    assert result == 2
    output = capsys.readouterr().out
    assert "status=blocked" in output
    assert "ambiguous_candidates" in output
    scheduler.preview_v4_morning_reset_recovery.assert_not_called()
    scheduler.apply_v4_morning_reset_recovery.assert_not_called()


def test_manual_recovery_one_shot_previews_then_applies_exact_plan(capsys):
    scheduler = Mock()
    scheduler.preview_v4_morning_reset_recovery.return_value = _preview()
    scheduler.apply_v4_morning_reset_recovery.return_value = {
        "status": "completed",
        "business_date": "2030-08-10",
        "plan_hash": "a" * 64,
        "applied_count": 1,
        "already_correct_count": 1,
        "excluded_clear_count": 3,
    }
    now = datetime(2030, 8, 10, 7, tzinfo=timezone.utc)
    with patch.object(
        _MODULE,
        "_manual_recovery_scan",
        return_value=([("tenant-a", date(2030, 8, 10), "previous_day_gemini_not_verified")], {
            "v4_tenants": 1,
            "carry_absent": 0,
            "carry_completed": 0,
            "carry_eligible": 1,
            "other_errors": {},
        }),
    ):
        result = _run_manual_recovery_current_day(
            scheduler,
            frozenset({"tenant-a"}),
            now=now,
        )

    assert result == 0
    scheduler.preview_v4_morning_reset_recovery.assert_called_once_with(
        "tenant-a",
        date(2030, 8, 10),
        now,
    )
    scheduler.apply_v4_morning_reset_recovery.assert_called_once_with(
        "tenant-a",
        date(2030, 8, 10),
        plan_hash="a" * 64,
        now=now,
    )
    output = capsys.readouterr().out
    assert "status=preview_ready" in output
    assert "safe_operations=2" in output
    assert "excluded_clears=3" in output
    assert "status=completed" in output
    assert "applied=1" in output




def test_manual_recovery_blocks_before_gemini_when_catalog_review_is_pending(capsys):
    scheduler = Mock()
    now = datetime(2030, 8, 10, 7, tzinfo=timezone.utc)
    with patch.object(
        _MODULE,
        "_manual_recovery_scan",
        return_value=([(
            "tenant-a",
            date(2030, 8, 10),
            "carry_forward_plan_has_issues",
        )], {
            "v4_tenants": 1,
            "carry_absent": 0,
            "carry_completed": 0,
            "carry_eligible": 1,
            "other_errors": {},
            "plan_issue_codes": {"MISSING_CATALOG_ITEMS": 1},
            "missing_catalog_materials": 22,
        }),
    ), patch.object(
        _MODULE,
        "_pending_carry_forward_materials",
        return_value=["SỮA ĐẶC*", "SỮA TƯƠI*"],
    ):
        result = _run_manual_recovery_current_day(
            scheduler,
            frozenset({"tenant-a"}),
            now=now,
        )

    assert result == 2
    scheduler.preview_v4_morning_reset_recovery.assert_not_called()
    scheduler.apply_v4_morning_reset_recovery.assert_not_called()
    output = capsys.readouterr().out
    assert "stage=preflight" in output
    assert "error_code=carry_forward_catalog_review_pending" in output
    assert "pending_catalog_materials=2" in output


def test_manual_recovery_prior_plan_issues_only_repreviews_on_first_pass(capsys):
    scheduler = Mock()
    scheduler.preview_v4_morning_reset_recovery.return_value = _preview()
    now = datetime(2030, 8, 10, 7, tzinfo=timezone.utc)
    with patch.object(
        _MODULE,
        "_manual_recovery_scan",
        return_value=([(
            "tenant-a",
            date(2030, 8, 10),
            "carry_forward_plan_has_issues",
        )], {
            "v4_tenants": 1,
            "carry_absent": 0,
            "carry_completed": 0,
            "carry_eligible": 1,
            "other_errors": {},
            "plan_issue_codes": {},
            "missing_catalog_materials": 0,
        }),
    ), patch.object(
        _MODULE,
        "_pending_carry_forward_materials",
        return_value=[],
    ):
        result = _run_manual_recovery_current_day(
            scheduler,
            frozenset({"tenant-a"}),
            now=now,
        )

    assert result == 0
    scheduler.preview_v4_morning_reset_recovery.assert_called_once()
    scheduler.apply_v4_morning_reset_recovery.assert_not_called()
    output = capsys.readouterr().out
    assert "status=preview_ready" in output
    assert "status=preview_only" in output
    assert "prior_plan_issues_requires_second_pass" in output




def test_manual_recovery_empty_plan_error_only_repreviews_on_first_pass(capsys):
    scheduler = Mock()
    scheduler.preview_v4_morning_reset_recovery.return_value = _preview()
    now = datetime(2030, 8, 10, 7, tzinfo=timezone.utc)
    with patch.object(
        _MODULE,
        "_manual_recovery_scan",
        return_value=([(
            "tenant-a",
            date(2030, 8, 10),
            "empty_carry_forward_plan",
        )], {
            "v4_tenants": 1,
            "carry_absent": 0,
            "carry_completed": 0,
            "carry_eligible": 1,
            "other_errors": {},
            "plan_issue_codes": {"NO_RESET_RULE": 1},
        }),
    ):
        result = _run_manual_recovery_current_day(
            scheduler,
            frozenset({"tenant-a"}),
            now=now,
        )

    assert result == 0
    scheduler.preview_v4_morning_reset_recovery.assert_called_once()
    scheduler.apply_v4_morning_reset_recovery.assert_not_called()
    output = capsys.readouterr().out
    assert "status=preview_ready" in output
    assert "status=preview_only" in output
    assert "prior_plan_issues_requires_second_pass" in output




def test_manual_recovery_closing_opening_mismatch_only_repreviews_on_first_pass(capsys):
    scheduler = Mock()
    scheduler.preview_v4_morning_reset_recovery.return_value = _preview()
    now = datetime(2030, 8, 10, 7, tzinfo=timezone.utc)
    with patch.object(
        _MODULE,
        "_manual_recovery_scan",
        return_value=([(
            "tenant-a",
            date(2030, 8, 10),
            "closing_opening_mismatch",
        )], {
            "v4_tenants": 1,
            "carry_absent": 0,
            "carry_completed": 0,
            "carry_eligible": 1,
            "other_errors": {},
            "plan_issue_codes": {"NO_RESET_RULE": 1},
        }),
    ):
        result = _run_manual_recovery_current_day(
            scheduler,
            frozenset({"tenant-a"}),
            now=now,
        )

    assert result == 0
    scheduler.preview_v4_morning_reset_recovery.assert_called_once()
    scheduler.apply_v4_morning_reset_recovery.assert_not_called()
    output = capsys.readouterr().out
    assert "status=preview_ready" in output
    assert "status=preview_only" in output
    assert "prior_plan_issues_requires_second_pass" in output


def test_manual_recovery_identity_errors_only_repreview_on_first_pass(capsys):
    now = datetime(2030, 8, 10, 7, tzinfo=timezone.utc)
    for error_code in ("unknown_material", "unknown_warehouse"):
        scheduler = Mock()
        scheduler.preview_v4_morning_reset_recovery.return_value = _preview()
        with patch.object(
            _MODULE,
            "_manual_recovery_scan",
            return_value=([(
                "tenant-a",
                date(2030, 8, 10),
                error_code,
            )], {
                "v4_tenants": 1,
                "carry_absent": 0,
                "carry_completed": 0,
                "carry_eligible": 1,
                "other_errors": {},
                "plan_issue_codes": {"NO_RESET_RULE": 1},
            }),
        ):
            result = _run_manual_recovery_current_day(
                scheduler,
                frozenset({"tenant-a"}),
                now=now,
            )

        assert result == 0
        scheduler.preview_v4_morning_reset_recovery.assert_called_once()
        scheduler.apply_v4_morning_reset_recovery.assert_not_called()
        output = capsys.readouterr().out
        assert "status=preview_ready" in output
        assert "status=preview_only" in output
        assert "prior_plan_issues_requires_second_pass" in output


def test_manual_recovery_one_shot_rejects_invalid_preview_without_apply(capsys):
    scheduler = Mock()
    preview = _preview()
    preview["operations"] = preview["operations"][:1]
    scheduler.preview_v4_morning_reset_recovery.return_value = preview
    with patch.object(
        _MODULE,
        "_manual_recovery_scan",
        return_value=([("tenant-a", date(2030, 8, 10), "previous_day_gemini_not_verified")], {
            "v4_tenants": 1,
            "carry_absent": 0,
            "carry_completed": 0,
            "carry_eligible": 1,
            "other_errors": {},
        }),
    ):
        result = _run_manual_recovery_current_day(
            scheduler,
            frozenset({"tenant-a"}),
            now=datetime(2030, 8, 10, 7, tzinfo=timezone.utc),
        )

    assert result == 2
    assert "inventory_manual_recovery_preview_invalid" in capsys.readouterr().out
    scheduler.apply_v4_morning_reset_recovery.assert_not_called()
