"""Bounded cross-worker cooldown prevents repeated fast Codex CLI failures."""
from types import SimpleNamespace

from app.modules.realistic_review_ugc import codex_account_manager as pool


def test_early_stage1_cli_failure_temporarily_uses_other_connected_profile(tmp_path, monkeypatch):
    root = tmp_path / "codex"
    secondary = tmp_path / "codex-account-2"
    (secondary / "skills" / "creative-brief-v3").mkdir(parents=True)
    (secondary / "skills" / "creative-brief-v3" / "SKILL.md").write_text(
        "---\nname: creative-brief-v3\ndescription: test\n---\n",
        encoding="utf-8",
    )
    rows = [
        SimpleNamespace(account_id="account-1", enabled=True,
                        stages_json=["stage1"], max_concurrent=1),
        SimpleNamespace(account_id="account-2", enabled=True,
                        stages_json=["stage1"], max_concurrent=1),
    ]
    monkeypatch.setattr(pool, "ensure_accounts", lambda *_args: rows)
    monkeypatch.setattr(pool, "auth_status", lambda *_args: "connected")
    with pool.acquire_worker_profile(SimpleNamespace(), "tenant",
                                     "stage1", str(root), "codex", "creative-brief-v3") as (name, home):
        assert name == "account-2"
    pool.mark_stage1_profile_cooldown(secondary)
    assert pool.stage1_profile_cooling(secondary)
    with pool.acquire_worker_profile(SimpleNamespace(), "tenant",
                                     "stage1", str(root), "codex", "creative-brief-v3") as (name, home):
        assert name == "account-1"
    # Cooldown must not change other product workflows.
    with pool.acquire_worker_profile(SimpleNamespace(), "tenant",
                                     "stage1", str(root), "codex", "other-skill") as (name, home):
        assert name == "account-1"
    marker = secondary / "worker-locks" / pool._STAGE1_EARLY_FAILURE_MARKER
    marker.write_text("1", encoding="ascii")
    assert not pool.stage1_profile_cooling(secondary)
    with pool.acquire_worker_profile(SimpleNamespace(), "tenant",
                                     "stage1", str(root), "codex", "creative-brief-v3") as (name, home):
        assert name == "account-2"


def test_cooldown_untrusted_marker_is_not_extended_indefinitely(tmp_path):
    home = tmp_path / "account"
    marker = home / "worker-locks" / pool._STAGE1_EARLY_FAILURE_MARKER
    marker.parent.mkdir(parents=True)
    marker.write_text("not a timestamp", encoding="ascii")
    assert not pool.stage1_profile_cooling(home)
    marker.write_text("9999999999", encoding="ascii")
    assert not pool.stage1_profile_cooling(home)
