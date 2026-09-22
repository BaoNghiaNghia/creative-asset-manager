from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

API_ROOT = Path(__file__).resolve().parents[2] / "api"
ENCODER_ROOT = Path(__file__).resolve().parents[1]
for path in (str(API_ROOT), str(ENCODER_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from app.modules.visual_search.model_spec import SIGLIP2_MODEL, SIGLIP2_REVISION
from provision_model import _REQUIRED_FILES, provision_model


def test_provision_model_downloads_exact_pinned_snapshot(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    def fake_snapshot_download(**kwargs):
        calls.append(kwargs)
        target = Path(kwargs["local_dir"])
        for name in _REQUIRED_FILES:
            (target / name).write_bytes(b"x")
        return str(target)

    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(snapshot_download=fake_snapshot_download),
    )

    target = provision_model(tmp_path)

    assert target == tmp_path / SIGLIP2_REVISION
    assert calls == [{
        "repo_id": SIGLIP2_MODEL,
        "revision": SIGLIP2_REVISION,
        "local_dir": target,
    }]
