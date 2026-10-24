"""Regression test for provider policy JSON notification comparison."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def test_provider_policy_notification_compares_jsonb_not_json():
    migration = (
        Path(__file__).resolve().parents[5]
        / "database/migrations/versions/0181_provider_policy_json_wakeup.py"
    )
    spec = spec_from_file_location("provider_policy_json_wakeup", migration)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    captured = []
    with patch.object(
        module.op, "get_bind",
        return_value=SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    ), patch.object(module.op, "execute", side_effect=captured.append):
        module.upgrade()
    assert len(captured) == 1
    statement = captured[0]
    assert "CREATE OR REPLACE FUNCTION" in statement
    assert "NEW.allowed_models_json::jsonb IS DISTINCT FROM OLD.allowed_models_json::jsonb" in statement
    assert "NEW.allowed_models_json IS DISTINCT FROM OLD.allowed_models_json" not in statement
    assert "pg_notify('cam_processing_queue_wakeup', '*')" in statement
