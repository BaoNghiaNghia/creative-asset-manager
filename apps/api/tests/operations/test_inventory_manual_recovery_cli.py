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
        "_manual_recovery_candidates",
        return_value=[],
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


def test_manual_recovery_one_shot_blocks_ambiguous_candidates(capsys):
    scheduler = Mock()
    with patch.object(
        _MODULE,
        "_manual_recovery_candidates",
        return_value=[
            ("tenant-a", date(2030, 8, 10)),
            ("tenant-b", date(2030, 8, 10)),
        ],
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
        "_manual_recovery_candidates",
        return_value=[("tenant-a", date(2030, 8, 10))],
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


def test_manual_recovery_one_shot_rejects_invalid_preview_without_apply(capsys):
    scheduler = Mock()
    preview = _preview()
    preview["operations"] = preview["operations"][:1]
    scheduler.preview_v4_morning_reset_recovery.return_value = preview
    with patch.object(
        _MODULE,
        "_manual_recovery_candidates",
        return_value=[("tenant-a", date(2030, 8, 10))],
    ):
        result = _run_manual_recovery_current_day(
            scheduler,
            frozenset({"tenant-a"}),
            now=datetime(2030, 8, 10, 7, tzinfo=timezone.utc),
        )

    assert result == 2
    assert "inventory_manual_recovery_preview_invalid" in capsys.readouterr().out
    scheduler.apply_v4_morning_reset_recovery.assert_not_called()
