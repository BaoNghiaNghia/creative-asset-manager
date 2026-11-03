"""G3 pilot readiness must stay read-only, safe and fail closed."""
import importlib.util
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[5] / "scripts" / "cam-g3-readiness.py"
spec = importlib.util.spec_from_file_location("cam_g3_readiness", SOURCE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def release_tree(tmp_path):
    root = tmp_path / "runtime"
    releases = root / "releases"
    for name in ("old", "new"):
        executable = releases / name / "apps/api/.venv/bin/python"
        executable.parent.mkdir(parents=True)
        executable.touch()
    (root / "current").symlink_to(releases / "new")
    (root / "previous").symlink_to(releases / "old")
    return root


def values():
    return {
        "TYPOGRAPHY_V2_ENABLED": "true",
        "TYPOGRAPHY_V2_CANARY_TENANT_IDS": "tenant-approved",
        "TYPOGRAPHY_V2_PILOT_WORKER_ENABLED": "true",
        "TYPOGRAPHY_V2_PILOT_ROLLBACK_VERIFIED": "true",
        # No provider keys or credentials should influence the report.
        "GEMINI_API_KEY": "top-secret-do-not-log",
    }


def test_g3_readiness_fails_closed_with_no_canary_or_flags():
    report = module.readiness_report({}, tenant_id="tenant-approved")
    assert report["status"] == "blocked"
    assert "explicit_canary_present" in report["blocking_checks"]
    assert "pilot_worker_enabled" in report["blocking_checks"]
    assert report["provider_calls"] == report["writes"] == 0


def test_even_full_config_cannot_replace_human_signoff(tmp_path):
    report = module.readiness_report(
        values(), tenant_id="tenant-approved",
        release_root=release_tree(tmp_path),
        disk_free_mib=9000, mem_available_mib=5000,
    )
    assert report["status"] == "pending_manual_signoff"
    assert not report["blocking_checks"]
    assert "eight_rights_verified_references" in report["manual_evidence_not_verified"]
    assert "top-secret" not in str(report)
    assert "tenant-approved" not in str(report)


def test_wrong_tenant_and_single_release_are_blocked(tmp_path):
    root = release_tree(tmp_path)
    (root / "previous").unlink()
    (root / "previous").symlink_to(root / "releases" / "new")
    report = module.readiness_report(
        values(), tenant_id="tenant-other", release_root=root,
        disk_free_mib=3000, mem_available_mib=4000,
    )
    assert "target_tenant_allowlisted" in report["blocking_checks"]
    assert "independent_backend_rollback_release" in report["blocking_checks"]


def test_low_capacity_and_flag_values_are_accurate(tmp_path):
    vals = values()
    vals["TYPOGRAPHY_V2_PILOT_ROLLBACK_VERIFIED"] = "false"
    report = module.readiness_report(
        vals, tenant_id="tenant-approved",
        release_root=release_tree(tmp_path),
        disk_free_mib=1000, mem_available_mib=2000,
    )
    assert "rollback_signoff_flag" in report["blocking_checks"]
    assert "disk_headroom_at_least_2gib" in report["blocking_checks"]
    assert "available_memory_at_least_3gib" in report["blocking_checks"]
    assert module.enabled("TRUE") and not module.enabled("off")
