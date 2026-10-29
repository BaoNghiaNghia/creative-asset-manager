"""G6.2 ImageGen human review is private, immutable and non-publishing."""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from PIL import Image, ImageDraw
import pytest

from app.modules.realistic_review_ugc.typography_imagegen_pilot import (
    PILOT_SCHEMA, TypographyPilotError, _store_new, fingerprint,
)
from app.modules.realistic_review_ugc.typography_imagegen_review import (
    REVIEW_SCHEMA, inspect_one_slot, review_one_slot,
)
from app.modules.realistic_review_ugc import typography_imagegen_review_once as cli


def settings(tmp_path, **kwargs):
    options = dict(
        TYPOGRAPHY_V2_ENABLED=True,
        TYPOGRAPHY_V2_IMAGEGEN_PILOT_ENABLED=True,
        TYPOGRAPHY_V2_PILOT_ROLLBACK_VERIFIED=True,
        TYPOGRAPHY_V2_CANARY_TENANT_IDS="tenant-a",
        IMAGE_GENERATION_ENABLED=True,
        CODEX_IMAGE_GENERATION_ENABLED=True,
        IMAGE_GENERATION_STAGING_ROOT=str(tmp_path),
    )
    options.update(kwargs)
    return SimpleNamespace(**options)


def plan():
    keyword = "CÀ PHÊ & SÀI GÒN"
    return dict(
        plan_version="concept-short-v2/plan-v1", skill="concept-short-v2",
        source_keyword_exact=keyword, preview_qa={"all_technical_pass": True},
        job_created=False, ready_for_imagegen=False,
        slots=[
            dict(slot=idx, status="pending", source_keyword_exact=keyword,
                 prompt="approved slot")
            for idx in range(1, 7)
        ],
    )


def image_bytes(opaque=False):
    image = Image.new("RGBA", (2048, 1024), (0, 0, 0, 255 if opaque else 0))
    ImageDraw.Draw(image).rectangle((340, 260, 1680, 765), fill=(25, 95, 135, 255))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def stage(tmp_path, *, slot=1, contents=None, source=None):
    source = source or plan()
    contents = contents if contents is not None else image_bytes()
    batch = str(uuid4())
    tenant_hash = sha256(b"tenant-a").hexdigest()[:20]
    root = tmp_path / "typography-v2-pilot"
    directory = root / tenant_hash / batch
    directory.mkdir(parents=True, mode=0o700)
    (root / ".imagegen-single-flight.lock").touch()
    digest = sha256(contents).hexdigest()
    record = {
        "schema_version": PILOT_SCHEMA, "slot": slot,
        "plan_sha256": fingerprint(source), "sha256": digest,
        "native_imagegen_verified": True, "technical_pass": True,
        "review": "pending", "image_spelling_verified": False,
        "eligible_for_publication": False, "stage1_output_saved": False,
    }
    _store_new(directory / f"design_{slot:02}.png", contents)
    _store_new(directory / f"slot_{slot:02}.json", json.dumps(record).encode())
    return batch, directory, digest, source


def approve(tmp_path, batch, digest, source, **changes):
    options = dict(
        settings=settings(tmp_path), tenant_id="tenant-a", batch_id=batch, slot=1,
        plan=source, reviewer_id="operator-001", decision="accepted",
        expected_sha256=digest, image_spelling_verified=True,
        originality_verified=True, embroidery_verified=True,
        revalidate=lambda: source,
    )
    options.update(changes)
    return review_one_slot(**options)


def test_manual_inspection_is_read_only_and_does_not_publish(tmp_path):
    batch, directory, digest, source = stage(tmp_path)
    result = inspect_one_slot(
        settings=settings(tmp_path), tenant_id="tenant-a",
        batch_id=batch, slot=1, plan=source,
    )
    assert result["sha256"] == digest
    assert result["image_path"] == str(directory / "design_01.png")
    assert result["review"] == "pending"
    assert result["eligible_for_publication"] is False
    assert not list(directory.glob("review_*.json"))


def test_explicit_three_way_accept_is_immutable_and_not_a_stage1_output(tmp_path):
    batch, directory, digest, source = stage(tmp_path)
    first_image = (directory / "design_01.png").read_bytes()
    original_manifest = (directory / "slot_01.json").read_bytes()
    result = approve(tmp_path, batch, digest, source)
    assert result == dict(
        status="review_recorded", slot=1, decision="accepted", sha256=digest,
        published=False, eligible_for_publication=False,
    )
    record = json.loads((directory / "review_01.json").read_text())
    assert record["schema_version"] == REVIEW_SCHEMA
    assert record["sha256"] == digest
    assert record["plan_sha256"] == fingerprint(source)
    assert record["decision"] == "accepted"
    assert record["reviewer_id"] == "operator-001"
    assert all(record[name] for name in (
        "image_spelling_verified", "originality_verified", "embroidery_verified",
    ))
    assert record["eligible_for_publication"] is False
    assert record["stage1_output_saved"] is False
    assert (directory / "design_01.png").read_bytes() == first_image
    assert (directory / "slot_01.json").read_bytes() == original_manifest
    with pytest.raises(TypographyPilotError) as exc:
        approve(tmp_path, batch, digest, source)
    assert exc.value.code == "already_reviewed"
    assert (directory / "review_01.json").read_text() == json.dumps(record, sort_keys=True) + "\n"


@pytest.mark.parametrize("omission", [
    "image_spelling_verified", "originality_verified", "embroidery_verified",
])
def test_accept_requires_each_human_confirmation(tmp_path, omission):
    batch, directory, digest, source = stage(tmp_path)
    with pytest.raises(TypographyPilotError) as exc:
        approve(tmp_path, batch, digest, source, **{omission: False})
    assert exc.value.code == "human_qa_missing"
    assert not list(directory.glob("review_*.json"))


def test_rejection_requires_bounded_reason_and_cannot_auto_publish(tmp_path):
    batch, directory, digest, source = stage(tmp_path)
    with pytest.raises(TypographyPilotError) as exc:
        approve(tmp_path, batch, digest, source, decision="rejected")
    assert exc.value.code == "rejection_reason_invalid"
    result = approve(
        tmp_path, batch, digest, source, decision="rejected",
        image_spelling_verified=False, originality_verified=False,
        embroidery_verified=False, reason_code="spelling",
    )
    record = json.loads((directory / "review_01.json").read_text())
    assert result["decision"] == "rejected"
    assert record["reason_code"] == "spelling"
    assert record["image_spelling_verified"] is False
    assert record["eligible_for_publication"] is False


def test_tampered_png_and_mismatched_sha_are_blocked(tmp_path):
    batch, directory, digest, source = stage(tmp_path)
    with pytest.raises(TypographyPilotError) as exc:
        approve(tmp_path, batch, "0" * 64, source)
    assert exc.value.code == "image_changed"
    (directory / "design_01.png").write_bytes(image_bytes(opaque=True))
    with pytest.raises(TypographyPilotError) as exc:
        approve(tmp_path, batch, digest, source)
    assert exc.value.code == "image_tampered"
    assert not list(directory.glob("review_*.json"))


def test_source_changed_or_rights_revoked_prevents_decision(tmp_path):
    batch, directory, digest, source = stage(tmp_path)
    changed = plan()
    changed["source_keyword_exact"] = "REVOKED"
    with pytest.raises(TypographyPilotError) as exc:
        approve(tmp_path, batch, digest, source, revalidate=lambda: changed)
    assert exc.value.code == "source_changed"
    with pytest.raises(TypographyPilotError) as exc:
        approve(tmp_path, batch, digest, changed)
    assert exc.value.code == "manifest_stale"
    assert not list(directory.glob("review_*.json"))


def test_disabled_canary_rejects_before_storage_access(tmp_path):
    options = settings(tmp_path, TYPOGRAPHY_V2_IMAGEGEN_PILOT_ENABLED=False)
    with pytest.raises(TypographyPilotError) as exc:
        inspect_one_slot(
            settings=options, tenant_id="tenant-a", batch_id=str(uuid4()),
            slot=1, plan=plan(),
        )
    assert exc.value.code == "pilot_disabled"
    assert not (tmp_path / "typography-v2-pilot").exists()


def test_cross_tenant_and_symlink_are_rejected(tmp_path):
    batch, directory, digest, source = stage(tmp_path)
    with pytest.raises(TypographyPilotError) as exc:
        inspect_one_slot(
            settings=settings(tmp_path, TYPOGRAPHY_V2_CANARY_TENANT_IDS="tenant-a,tenant-b"),
            tenant_id="tenant-b", batch_id=batch, slot=1, plan=source,
        )
    assert exc.value.code == "batch_missing"
    image = directory / "design_01.png"
    backup = directory / "backing.png"
    image.rename(backup)
    image.symlink_to(backup)
    with pytest.raises(TypographyPilotError) as exc:
        approve(tmp_path, batch, digest, source)
    assert exc.value.code == "slot_unavailable"
    assert not list(directory.glob("review_*.json"))


def test_reviewer_cannot_provide_free_text_or_invalid_hash(tmp_path):
    batch, directory, digest, source = stage(tmp_path)
    with pytest.raises(TypographyPilotError) as exc:
        approve(tmp_path, batch, digest, source, reviewer_id="")
    assert exc.value.code == "reviewer_invalid"
    with pytest.raises(TypographyPilotError) as exc:
        approve(tmp_path, batch, "invalid", source)
    assert exc.value.code == "sha256_invalid"
    with pytest.raises(TypographyPilotError) as exc:
        approve(tmp_path, batch, digest, source, reason_code="arbitrary notes")
    assert exc.value.code == "human_qa_missing"


def test_dry_run_never_loads_settings_or_database(monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_settings", lambda: pytest.fail("dry-run loaded settings"))
    rc = cli.main([
        "--tenant-id", "tenant-a", "--user-id", "operator",
        "--keyword-id", "keyword", "--reference-id", "reference",
        "--batch-id", str(uuid4()), "--slot", "1",
    ])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["status"] == "dry_run_only"
