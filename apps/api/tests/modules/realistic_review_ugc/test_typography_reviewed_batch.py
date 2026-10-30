"""G6.3 private partial ImageGen checkpoint; no Stage 1/Stage 2 publication."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from PIL import Image, ImageDraw
import pytest

from app.modules.realistic_review_ugc.typography_imagegen_pilot import (
    PILOT_SCHEMA, TypographyPilotError, _store_new, fingerprint,
)
from app.modules.realistic_review_ugc.typography_imagegen_review import (
    REVIEW_SCHEMA, review_one_slot,
)
from app.modules.realistic_review_ugc.typography_reviewed_batch import (
    BATCH_SCHEMA, inspect_reviewed_batch,
)
from app.modules.realistic_review_ugc import typography_reviewed_batch_once as cli
from app.modules.realistic_review_ugc.typography_quality_gate import TypographyQualityError


def settings(tmp_path, **extra):
    opts = {
        "TYPOGRAPHY_V2_ENABLED": True,
        "TYPOGRAPHY_V2_IMAGEGEN_PILOT_ENABLED": True,
        "TYPOGRAPHY_V2_PILOT_ROLLBACK_VERIFIED": True,
        "TYPOGRAPHY_V2_CANARY_TENANT_IDS": "tenant-a",
        "IMAGE_GENERATION_ENABLED": True,
        "CODEX_IMAGE_GENERATION_ENABLED": True,
        "IMAGE_GENERATION_STAGING_ROOT": str(tmp_path),
    }
    opts.update(extra)
    return SimpleNamespace(**opts)


def plan():
    phrase = "CÀ PHÊ & SÀI GÒN"
    return {
        "plan_version": "concept-short-v2/plan-v1",
        "skill": "concept-short-v2",
        "source_keyword_exact": phrase,
        "preview_qa": {"all_technical_pass": True},
        "job_created": False, "ready_for_imagegen": False,
        "slots": [{
            "slot": i, "status": "pending", "source_keyword_exact": phrase,
            "prompt": f"concept {i}",
        } for i in range(1, 7)],
    }


def image_bytes(slot):
    image = Image.new("RGBA", (2048, 1024), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle(
        (250 + slot * 10, 240, 1730 - slot * 7, 760),
        fill=(20 + slot * 22, 40 + slot * 15, 145 - slot * 8, 255),
    )
    stream = BytesIO()
    image.save(stream, "PNG")
    return stream.getvalue()


def batch(tmp_path, *, tenant="tenant-a"):
    batch_id = str(uuid4())
    root = tmp_path / "typography-v2-pilot"
    folder = root / sha256(tenant.encode()).hexdigest()[:20] / batch_id
    folder.mkdir(parents=True)
    (root / ".imagegen-single-flight.lock").touch()
    return batch_id, folder


def write_slot(folder, slot, current, *, content=None, approval="accepted"):
    data = content if content is not None else image_bytes(slot)
    digest = sha256(data).hexdigest()
    _store_new(folder / f"design_{slot:02}.png", data)
    _store_new(folder / f"slot_{slot:02}.json", json.dumps({
        "schema_version": PILOT_SCHEMA, "slot": slot,
        "plan_sha256": fingerprint(current),
        "sha256": digest, "native_imagegen_verified": True,
        "technical_pass": True, "review": "pending",
        "image_spelling_verified": False,
        "eligible_for_publication": False, "stage1_output_saved": False,
    }).encode())
    if approval is not None:
        approved = approval == "accepted"
        _store_new(folder / f"review_{slot:02}.json", json.dumps({
            "schema_version": REVIEW_SCHEMA, "slot": slot,
            "plan_sha256": fingerprint(current), "sha256": digest,
            "decision": approval, "reviewer_id": "human-reviewer",
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "image_spelling_verified": approved,
            "originality_verified": approved, "embroidery_verified": approved,
            "reason_code": "approved" if approved else "spelling",
            "eligible_for_publication": False, "stage1_output_saved": False,
        }).encode())
    return digest


def inspect(tmp_path, batch_id, current, **kwargs):
    values = dict(
        settings=settings(tmp_path), tenant_id="tenant-a",
        batch_id=batch_id, plan=current, revalidate=lambda: current,
    )
    values.update(kwargs)
    return inspect_reviewed_batch(**values)


def test_no_generated_images_returns_six_pending_and_never_publishes(tmp_path):
    batch_id, folder = batch(tmp_path)
    current = plan()
    first_files = list(folder.iterdir())
    result = inspect(tmp_path, batch_id, current)
    assert result["schema_version"] == BATCH_SCHEMA
    assert result["source_keyword_exact"] == "CÀ PHÊ & SÀI GÒN"
    assert result["requested_slots"] == 6
    assert result["accepted_count"] == 0
    assert result["reviewed_images"] == []
    assert result["slot_contract"]["pending_slots"] == [1, 2, 3, 4, 5, 6]
    assert result["slot_contract"]["actual_count"] == 0
    assert result["private_only"] is True
    assert result["published"] is False
    assert result["eligible_for_publication"] is False
    assert result["stage1_output_saved"] is False
    assert result["stage2_handoff_performed"] is False
    assert list(folder.iterdir()) == first_files


@pytest.mark.parametrize("count", [1, 2, 3, 4, 5])
def test_genuine_one_to_five_accepted_images_are_preserved_without_filling_slots(tmp_path, count):
    batch_id, folder = batch(tmp_path)
    current = plan()
    for i in range(1, count + 1):
        write_slot(folder, i, current)
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    result = inspect(tmp_path, batch_id, current)
    assert result["accepted_count"] == count
    assert [row["slot"] for row in result["reviewed_images"]] == list(range(1, count + 1))
    assert result["slot_contract"]["eligible_slots"] == list(range(1, count + 1))
    assert result["slot_contract"]["pending_slots"] == list(range(count + 1, 7))
    assert result["slot_contract"]["partial"] is True
    assert result["slot_contract"]["completed"] is False
    assert result["stage1_output_saved"] is False
    assert result["stage2_handoff_performed"] is False
    assert result["eligible_for_publication"] is False
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before


def test_six_distinct_reviewed_slots_report_complete_but_stay_private(tmp_path):
    batch_id, folder = batch(tmp_path)
    current = plan()
    for i in range(1, 7):
        write_slot(folder, i, current)
    result = inspect(tmp_path, batch_id, current)
    assert result["slot_contract"]["completed"] is True
    assert result["accepted_count"] == 6
    assert result["slot_contract"]["partial"] is False
    assert result["eligible_for_publication"] is False
    assert result["stage1_output_saved"] is False


def test_review_pending_and_rejected_are_never_in_accepted_count(tmp_path):
    batch_id, folder = batch(tmp_path)
    current = plan()
    write_slot(folder, 1, current)
    write_slot(folder, 2, current, approval="rejected")
    write_slot(folder, 3, current, approval=None)
    result = inspect(tmp_path, batch_id, current)
    assert result["accepted_count"] == 1
    assert result["slot_contract"]["eligible_slots"] == [1]
    assert result["slot_contract"]["failed_slots"] == [2]
    assert result["slot_contract"]["needs_review_slots"] == [3]
    assert result["slot_contract"]["pending_slots"] == [4, 5, 6]
    assert result["eligible_for_publication"] is False


def test_five_approved_and_one_rejected_are_partial_not_synthetic_success(tmp_path):
    batch_id, folder = batch(tmp_path)
    current = plan()
    for i in range(1, 6):
        write_slot(folder, i, current)
    write_slot(folder, 6, current, approval="rejected")
    result = inspect(tmp_path, batch_id, current)
    assert result["slot_contract"]["status"] == "partial"
    assert result["slot_contract"]["actual_count"] == 5
    assert result["slot_contract"]["completed"] is False
    assert result["slot_contract"]["failed_slots"] == [6]
    assert len(result["reviewed_images"]) == 5
    assert result["eligible_for_publication"] is False


def test_duplicate_accepted_sha_never_fills_two_slots(tmp_path):
    batch_id, folder = batch(tmp_path)
    current = plan()
    same = image_bytes(1)
    write_slot(folder, 1, current, content=same)
    write_slot(folder, 2, current, content=same)
    with pytest.raises(TypographyQualityError, match="duplicate"):
        inspect(tmp_path, batch_id, current)


def test_file_tamper_manifest_mismatch_and_symlink_fail_closed(tmp_path):
    batch_id, folder = batch(tmp_path)
    current = plan()
    write_slot(folder, 1, current)
    image_path = folder / "design_01.png"
    image_path.write_bytes(image_bytes(2))
    with pytest.raises(TypographyPilotError) as exc:
        inspect(tmp_path, batch_id, current)
    assert exc.value.code == "image_tampered"
    image_path.unlink()
    image_path.symlink_to(folder / "unavailable.png")
    with pytest.raises(TypographyPilotError) as exc:
        inspect(tmp_path, batch_id, current)
    assert exc.value.code == "slot_unavailable"


def test_incomplete_checkpoint_malformed_review_and_rejection_missing_reason_blocked(tmp_path):
    batch_id, folder = batch(tmp_path)
    current = plan()
    _store_new(folder / "review_02.json", b"not-json")
    with pytest.raises(TypographyPilotError) as exc:
        inspect(tmp_path, batch_id, current)
    assert exc.value.code == "checkpoint_incomplete"
    (folder / "review_02.json").unlink()
    write_slot(folder, 1, current)
    (folder / "review_01.json").write_bytes(b"not-json")
    with pytest.raises(TypographyPilotError) as exc:
        inspect(tmp_path, batch_id, current)
    assert exc.value.code == "review_invalid"
    record = json.dumps({
        "schema_version": REVIEW_SCHEMA, "slot": 1,
        "plan_sha256": fingerprint(current),
        "sha256": sha256(image_bytes(1)).hexdigest(),
        "decision": "rejected", "reviewer_id": "operator",
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "reason_code": "approved", "image_spelling_verified": False,
        "originality_verified": False, "embroidery_verified": False,
        "eligible_for_publication": False, "stage1_output_saved": False,
    }).encode()
    (folder / "review_01.json").write_bytes(record)
    with pytest.raises(TypographyPilotError) as exc:
        inspect(tmp_path, batch_id, current)
    assert exc.value.code == "review_invalid"


def test_revoked_rights_changed_source_or_disabled_tenant_block_batch_inspection(tmp_path):
    batch_id, folder = batch(tmp_path)
    current = plan()
    write_slot(folder, 1, current)
    changed = plan()
    changed["source_keyword_exact"] = "REVOKED"
    with pytest.raises(TypographyPilotError) as exc:
        inspect(tmp_path, batch_id, current, revalidate=lambda: changed)
    assert exc.value.code == "source_changed"
    with pytest.raises(TypographyPilotError) as exc:
        inspect(tmp_path, batch_id, changed)
    assert exc.value.code == "manifest_stale"
    with pytest.raises(TypographyPilotError) as exc:
        inspect(
            tmp_path, batch_id, current,
            settings=settings(tmp_path, TYPOGRAPHY_V2_ENABLED=False),
        )
    assert exc.value.code == "pilot_disabled"


def test_cross_tenant_manifest_read_fails_before_image_access(tmp_path):
    batch_id, folder = batch(tmp_path)
    current = plan()
    write_slot(folder, 1, current)
    with pytest.raises(TypographyPilotError) as exc:
        inspect(
            tmp_path, batch_id, current, tenant_id="tenant-b",
            settings=settings(tmp_path, TYPOGRAPHY_V2_CANARY_TENANT_IDS="tenant-a,tenant-b"),
        )
    assert exc.value.code == "batch_missing"


def test_invalid_reviewer_signature_and_missing_revalidator_rejected(tmp_path):
    batch_id, folder = batch(tmp_path)
    current = plan()
    write_slot(folder, 1, current)
    with pytest.raises(TypographyPilotError) as exc:
        inspect(tmp_path, batch_id, current, revalidate=None)
    assert exc.value.code == "source_invalid"
    path = folder / "review_01.json"
    record = json.loads(path.read_text())
    record["image_spelling_verified"] = False
    path.write_text(json.dumps(record))
    with pytest.raises(TypographyPilotError) as exc:
        inspect(tmp_path, batch_id, current)
    assert exc.value.code == "review_invalid"


def test_batch_cli_default_dry_run_does_not_load_settings_or_touch_db(monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_settings", lambda: pytest.fail("dry-run loaded settings"))
    result = cli.main([
        "--tenant-id", "tenant-a", "--user-id", "operator",
        "--keyword-id", "keyword", "--reference-id", "reference",
        "--batch-id", str(uuid4()),
    ])
    assert result == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "dry_run_only"
    assert output["published"] is False
    assert output["stage2_jobs"] == 0


def test_batch_cli_rejects_invalid_ids_before_loading_private_db(monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_settings", lambda: pytest.fail("invalid IDs loaded settings"))
    result = cli.main([
        "--tenant-id", "tenant-a", "--user-id", "operator",
        "--keyword-id", "keyword", "--reference-id", "reference",
        "--batch-id", "not-a-uuid", "--execute",
    ])
    assert result == 2
    assert json.loads(capsys.readouterr().out)["status"] == "batch_invalid"



@pytest.mark.parametrize("count", [1, 2, 3, 4, 5, 6])
def test_reviewed_selection_preview_preserves_exact_slots_without_publication(tmp_path, count):
    batch_id, folder = batch(tmp_path)
    current = plan()
    for slot in range(1, count + 1):
        write_slot(folder, slot, current)
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    chosen = list(range(1, count + 1))
    result = inspect(tmp_path, batch_id, current, keyword_id="keyword-1", selected_slots=chosen)
    preview = result["selection_preview"]
    assert preview["selected_slots"] == chosen
    assert preview["selected_count"] == count
    assert [i["slot"] for i in preview["selected_images"]] == chosen
    assert all(len(i["sha256"]) == 64 for i in preview["selected_images"])
    assert preview["private_only"] is True
    assert preview["published"] is False
    assert preview["stage1_output_saved"] is False
    assert preview["stage2_handoff_performed"] is False
    assert preview["can_queue_stage2"] is False
    assert preview["requires_managed_storage_commit"] is True
    assert "private_image_path" not in str(preview)
    assert inspect(tmp_path, batch_id, current, keyword_id="keyword-1",
                   selected_slots=list(reversed(chosen)))["selection_preview"]["selection_sha256"] == preview["selection_sha256"]
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before


def test_reviewed_selection_rejects_pending_rejected_duplicate_and_invalid_slots(tmp_path):
    batch_id, folder = batch(tmp_path)
    current = plan()
    write_slot(folder, 1, current)
    write_slot(folder, 2, current, approval="rejected")
    write_slot(folder, 3, current, approval=None)
    for slots, code in [
        ([2], "selection_not_approved"), ([3], "selection_not_approved"),
        ([4], "selection_not_approved"), ([1, 1], "selection_invalid"),
        ([], "selection_invalid"), ([True], "selection_invalid"),
        ([0], "selection_invalid"), ([7], "selection_invalid"),
        ("1", "selection_invalid"),
    ]:
        with pytest.raises(TypographyPilotError) as exc:
            inspect(tmp_path, batch_id, current, keyword_id="keyword-1", selected_slots=slots)
        assert exc.value.code == code
    with pytest.raises(TypographyPilotError) as exc:
        inspect(tmp_path, batch_id, current, selected_slots=[1])
    assert exc.value.code == "selection_invalid"


def test_selection_fingerprint_binds_tenant_keyword_and_selected_image(tmp_path):
    batch_id, folder = batch(tmp_path)
    current = plan()
    write_slot(folder, 1, current)
    a = inspect(tmp_path, batch_id, current, keyword_id="keyword-a",
                selected_slots=[1])["selection_preview"]["selection_sha256"]
    b = inspect(tmp_path, batch_id, current, keyword_id="keyword-b",
                selected_slots=[1])["selection_preview"]["selection_sha256"]
    assert a != b
    batch_id_2, folder_2 = batch(tmp_path)
    write_slot(folder_2, 1, current)
    c = inspect(tmp_path, batch_id_2, current, keyword_id="keyword-a",
                selected_slots=[1])["selection_preview"]["selection_sha256"]
    assert a != c


def test_selection_revalidates_rights_before_returning_handoff_preview(tmp_path):
    batch_id, folder = batch(tmp_path)
    current = plan()
    write_slot(folder, 1, current)
    stale = plan()
    stale["source_keyword_exact"] = "WRONG"
    with pytest.raises(TypographyPilotError) as exc:
        inspect(tmp_path, batch_id, current, keyword_id="keyword-1",
                selected_slots=[1], revalidate=lambda: stale)
    assert exc.value.code == "source_changed"
