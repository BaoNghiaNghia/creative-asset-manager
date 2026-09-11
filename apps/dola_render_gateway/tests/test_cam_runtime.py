import asyncio
import io
import json
import py_compile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cam_runtime.app import create_app
from cam_runtime.config import RuntimeConfigurationError, load_settings
from cam_runtime.paths import RuntimePathError, RuntimePaths
from cam_runtime.provisioning import read_provisioning_payload


def env(tmp_path: Path, **extra: str) -> dict[str, str]:
    values = {
        "DOLA_INTERNAL_API_KEY": "local-internal-key",
        "DOLA_STATE_ROOT": str(tmp_path / "state"),
        "DOLA_RUNTIME_ROOT": str(tmp_path / "run"),
    }
    values.update(extra)
    return values


def test_defaults_are_loopback_and_port_8100(tmp_path: Path):
    settings = load_settings(env(tmp_path), source_root=tmp_path / "source")
    assert settings.host == "127.0.0.1"
    assert settings.port == 8100


def test_non_loopback_requires_explicit_unsafe_opt_in(tmp_path: Path):
    with pytest.raises(RuntimeConfigurationError, match="loopback"):
        load_settings(env(tmp_path, DOLA_GATEWAY_HOST="0.0.0.0"), source_root=tmp_path / "source")
    settings = load_settings(
        env(tmp_path, DOLA_GATEWAY_HOST="0.0.0.0", DOLA_ALLOW_NON_LOOPBACK="true"),
        source_root=tmp_path / "source",
    )
    assert settings.host == "0.0.0.0"


@pytest.mark.parametrize("key", ["", "   ", "changeme", "secret", "test", "example"])
def test_missing_empty_and_placeholder_keys_are_rejected(tmp_path: Path, key: str):
    with pytest.raises(RuntimeConfigurationError):
        load_settings(env(tmp_path, DOLA_INTERNAL_API_KEY=key), source_root=tmp_path / "source")


def test_paths_are_absolute_and_persistent_state_stays_outside_source(tmp_path: Path):
    settings = load_settings(env(tmp_path), source_root=tmp_path / "source")
    paths = settings.paths
    assert paths.tasks_db == paths.state_root / "db" / "tasks.db"
    assert paths.pool_usage_db == paths.state_root / "db" / "pool_usage.db"
    for path in (paths.accounts_dir, paths.profiles_dir, paths.downloads_dir, paths.artifacts_dir):
        assert path.is_relative_to(paths.state_root)
    assert not paths.tasks_db.is_relative_to(paths.source_root)
    assert "local-internal-key" not in repr(settings.redacted)


def test_state_root_under_source_is_rejected(tmp_path: Path):
    source = tmp_path / "source"
    with pytest.raises(RuntimePathError, match="must not resolve under the source checkout"):
        RuntimePaths.from_roots(source_root=source, state_root=str(source / "state"), runtime_root=str(tmp_path / "run"))


def protected_upstream() -> FastAPI:
    app = FastAPI()

    @app.get("/protected")
    async def protected():
        return {"ok": True}

    return app


def test_bearer_boundary_and_liveness(tmp_path: Path):
    settings = load_settings(env(tmp_path), source_root=tmp_path / "source")
    client = TestClient(create_app(settings, upstream_app=protected_upstream()))
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/protected").status_code == 401
    assert client.get("/protected", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/protected", headers={"Authorization": "Bearer local-internal-key"}).json() == {"ok": True}


def test_safe_provisioning_payload_is_stdin_data_not_argv():
    payload = read_provisioning_payload(io.StringIO(json.dumps({
        "account": "acct", "email": "user@example.com", "password": "fake-password", "totp": "fake-totp",
    })))
    assert payload["account"] == "acct"
    source = (Path(__file__).parents[1] / "upstream" / "add_account.py").read_text()
    assert 'sys.argv[2].split("----")' not in source


def test_video_worker_is_runtime_reachable_and_compiles():
    root = Path(__file__).parents[1] / "upstream"
    assert "from video_worker_ui import" in (root / "browser_pool.py").read_text()
    assert "from video_worker import" in (root / "video_worker_ui.py").read_text()
    py_compile.compile(str(root / "video_worker.py"), doraise=True)
