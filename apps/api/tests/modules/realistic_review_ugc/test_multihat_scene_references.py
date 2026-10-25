"""Slot 5 uses independent, tenant-scoped samples of Cap Stack 3/4-hat photos."""
from __future__ import annotations

from io import BytesIO
import asyncio

import pytest
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.cap_stack_model import RrugcCapStackImageModel
from app.modules.realistic_review_ugc.multihat_scene_references import (
    MultiHatReferencesUnavailable, build_multihat_reference_sheets,
    select_multihat_references, validate_pinned_multihat_references,
)


@pytest.fixture
def cap_library():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    RrugcCapStackImageModel.__table__.create(engine)
    with Session(engine) as session:
        for group in range(3, 5):
            for i in range(35):
                session.add(RrugcCapStackImageModel(
                    id=f"tenant-a-{group}-{i}", tenant_id="tenant-a",
                    pin_key=str(group * 1000 + i),
                    pin_url=f"https://www.pinterest.com/pin/{group * 1000 + i}/",
                    image_url=f"https://i.pinimg.com/736x/{group}-{i}.jpg",
                    caps_detected=group, count_status="ready",
                    is_cap_stack=True, ai_suspected=False,
                ))
        # Humans can verify a cap count even when automated vision was uncertain.
        session.add(RrugcCapStackImageModel(
            id="human-verified", tenant_id="tenant-a", pin_key="9990",
            pin_url="https://www.pinterest.com/pin/9990/",
            image_url="https://i.pinimg.com/736x/human.jpg",
            caps_detected=6, manual_caps_count=3, manual_label="good",
            count_status="needs_review",
        ))
        for label in ("bad", "ai"):
            session.add(RrugcCapStackImageModel(
                id="rejected-" + label, tenant_id="tenant-a", pin_key="reject-" + label,
                pin_url="https://www.pinterest.com/pin/9999/",
                image_url=f"https://i.pinimg.com/736x/{label}.jpg",
                caps_detected=3, manual_label=label, count_status="ready",
                is_cap_stack=True, ai_suspected=False,
            ))
        session.add(RrugcCapStackImageModel(
            id="other-tenant", tenant_id="tenant-b", pin_key="other",
            pin_url="https://www.pinterest.com/pin/8888/",
            image_url="https://i.pinimg.com/736x/other.jpg",
            caps_detected=4, manual_label="good", manual_caps_count=4,
        ))
        session.add(RrugcCapStackImageModel(
            id="unverified", tenant_id="tenant-a", pin_key="unverified",
            pin_url="https://www.pinterest.com/pin/7777/",
            image_url="https://i.pinimg.com/736x/unverified.jpg",
            caps_detected=4, count_status="pending",
        ))
        session.add(RrugcCapStackImageModel(
            id="ai-suspected", tenant_id="tenant-a", pin_key="suspected",
            pin_url="https://www.pinterest.com/pin/6666/",
            image_url="https://i.pinimg.com/736x/suspected.jpg",
            caps_detected=3, count_status="ready", is_cap_stack=True,
            ai_suspected=True,
        ))
        session.commit()
        yield session
    engine.dispose()


def select(session, design, key="run-1"):
    return select_multihat_references(
        session, tenant_id="tenant-a", source_plan_id=design,
        source_revision="sha256:design-revision", random_key=key,
    )


def test_each_embroidery_design_has_a_reproducible_distinct_balanced_random_sample(cap_library):
    first = select(cap_library, "design-A")
    replay = select(cap_library, "design-A")
    second = select(cap_library, "design-B")
    new_run = select(cap_library, "design-A", "run-2")
    assert first == replay
    assert len(first) == len(second) == len(new_run) == 20
    assert {item["image_id"] for item in first} != {item["image_id"] for item in second}
    assert {item["image_id"] for item in first} != {item["image_id"] for item in new_run}
    assert sum(item["caps_count"] == 3 for item in first) == 10
    assert sum(item["caps_count"] == 4 for item in first) == 10
    assert all(item["image_id"] not in {
        "rejected-bad", "rejected-ai", "other-tenant", "unverified", "ai-suspected",
    } for item in first)
    assert validate_pinned_multihat_references(first) == first


def test_exactly_twenty_source_images_still_yield_design_specific_subsets():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    RrugcCapStackImageModel.__table__.create(engine)
    with Session(engine) as session:
        for i in range(20):
            session.add(RrugcCapStackImageModel(
                id=str(i), tenant_id="tenant-one", pin_key=str(i),
                pin_url=f"https://www.pinterest.com/pin/{i + 1}/",
                image_url=f"https://i.pinimg.com/736x/{i}.jpg",
                caps_detected=3 if i < 10 else 4,
                count_status="ready", is_cap_stack=True, ai_suspected=False,
            ))
        session.commit()
        samples = [
            select_multihat_references(
                session, tenant_id="tenant-one", source_plan_id=f"design-{design}",
                source_revision="rev", random_key="same-run",
            ) for design in range(12)
        ]
        assert all(len(items) == 19 for items in samples)
        assert len({tuple(sorted(ref["image_id"] for ref in items)) for items in samples}) > 1
    engine.dispose()


def test_pin_validation_rejects_duplicates_non_cdn_and_wrong_count(cap_library):
    pinned = select(cap_library, "design-A")
    assert not validate_pinned_multihat_references(pinned + [pinned[0]])
    tampered = [dict(item) for item in pinned]
    tampered[0]["image_url"] = "https://example.org/internal/file.png"
    assert not validate_pinned_multihat_references(tampered)
    tampered[0]["image_url"] = pinned[0]["image_url"]
    tampered[0]["caps_count"] = 7
    assert not validate_pinned_multihat_references(tampered)


def test_fewer_than_four_verified_scenes_blocks_queue():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    RrugcCapStackImageModel.__table__.create(engine)
    with Session(engine) as session:
        session.add(RrugcCapStackImageModel(
            tenant_id="only-one", pin_key="first",
            pin_url="https://www.pinterest.com/pin/100/",
            image_url="https://i.pinimg.com/736x/100.jpg",
            caps_detected=3, count_status="ready",
            is_cap_stack=True, ai_suspected=False,
        ))
        session.commit()
        with pytest.raises(MultiHatReferencesUnavailable, match="at least four"):
            select_multihat_references(
                session, tenant_id="only-one",
                source_plan_id="design-1", source_revision="revision",
            )
    engine.dispose()


def test_all_twenty_photos_are_condensed_into_four_imagegen_reference_sheets(monkeypatch, cap_library):
    from app.modules.realistic_review_ugc import multihat_scene_references as refs
    class FakeResponse:
        headers = {"content-type": "image/jpeg"}
        def __init__(self, content):
            self.content = content
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            return False
        def raise_for_status(self):
            return None
        async def aiter_bytes(self):
            yield self.content

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            return False
        def stream(self, method, url):
            assert method == "GET" and url.startswith("https://i.pinimg.com/")
            return FakeResponse(jpeg)

    picture = Image.new("RGB", (540, 390), "#8195a5")
    output = BytesIO()
    picture.save(output, "JPEG")
    jpeg = output.getvalue()
    monkeypatch.setattr(refs.httpx, "AsyncClient", FakeClient)
    selected = select(cap_library, "design-21")
    sheets, count = asyncio.run(build_multihat_reference_sheets(selected))
    assert count == 20
    assert len(sheets) == 4
    assert all(item.role == "multihat_scene_contact_sheet" for item in sheets)
    assert all(item.image.width == 1040 and item.image.height == 1320 for item in sheets)
