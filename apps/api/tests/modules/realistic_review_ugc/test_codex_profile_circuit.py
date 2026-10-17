from __future__ import annotations

from app.modules.realistic_review_ugc.codex_profile_circuit import (
    cooldown_remaining, note_failure, note_success,
)


def test_stage4_circuit_opens_after_three_rapid_provider_failures(tmp_path):
    home = str(tmp_path / "account2")
    assert cooldown_remaining(home, now=1000) == 0
    assert note_failure(home, "codex_image_provider_error", now=1000) == 0
    assert note_failure(home, "codex_image_provider_error", now=1020) == 0
    assert note_failure(home, "codex_image_provider_error", now=1040) == 1800
    assert cooldown_remaining(home, now=1080) == 1760
    assert cooldown_remaining(home, now=2841) == 0
    note_success(home)
    assert note_failure(home, "codex_image_provider_error", now=2900) == 0


def test_stage4_circuit_quota_opens_five_hour_window(tmp_path):
    home = str(tmp_path / "account2")
    assert note_failure(home, "codex_image_usage_limited", now=1000) == 18000
    assert cooldown_remaining(home, now=1001) == 17999
    assert note_failure(home, "other_error", now=1010) >= 17990


def test_stage4_circuit_timeout_window_resets_burst(tmp_path):
    home = str(tmp_path / "account2")
    assert note_failure(home, "codex_image_provider_error", now=1000) == 0
    assert note_failure(home, "codex_image_provider_error", now=1300) == 0
    assert note_failure(home, "codex_image_provider_error", now=1330) == 0
    assert cooldown_remaining(home, now=1340) == 0
