from datetime import datetime, timezone
from app.modules.realistic_review_ugc.loox_review_export import format_stage5_created_at, stage5_sample_review_date

def test_stage5_created_at_full_datetime():
    assert format_stage5_created_at(datetime(2026, 10, 22, 7, 8, 9, tzinfo=timezone.utc)) == "2026-10-22 07:08:09"

def test_stage5_created_at_missing_components_are_zero():
    assert format_stage5_created_at("2026-10-22") == "2026-10-22 00:00:00"
    assert format_stage5_created_at("2026-10-22 12:34") == "2026-10-22 12:34:00"
    assert format_stage5_created_at("2026-10") == "2026-10-00 00:00:00"
    assert format_stage5_created_at("2026") == "2026-00-00 00:00:00"
    assert format_stage5_created_at(None) == "0000-00-00 00:00:00"

def test_stage5_sample_date_has_seconds_and_zero_padding():
    value = stage5_sample_review_date("ca9fc78f-0ee6-4bc9-906d-c1a401b87359", datetime(2026, 10, 22, tzinfo=timezone.utc))
    assert len(value) == 19
    assert value[4] == "-" and value[7] == "-" and value[10] == " " and value[13] == ":" and value[16] == ":"

def test_stage5_sample_dates_are_distinct_dispersed_and_stable_for_review_ids():
    from uuid import uuid5, NAMESPACE_URL

    anchor = datetime(2026, 10, 22, 11, 59, 59, tzinfo=timezone.utc)
    ids = [str(uuid5(NAMESPACE_URL, f"stage5-sample-review-{i}")) for i in range(512)]
    samples = [stage5_sample_review_date(item_id, anchor) for item_id in ids]
    parsed = [datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc) for value in samples]

    assert len(set(samples)) == len(samples)
    assert all(datetime(2024, 10, 22, 11, 59, 59, tzinfo=timezone.utc) <= t <= anchor for t in parsed)
    assert len({t.year for t in parsed}) >= 2
    assert len({t.month for t in parsed}) == 12
    assert len({t.day for t in parsed}) >= 25
    assert len({t.hour for t in parsed}) >= 20
    assert len({t.minute for t in parsed}) >= 55
    assert len({t.second for t in parsed}) >= 55
    assert (max(parsed) - min(parsed)).days >= 600
    assert [stage5_sample_review_date(item_id, anchor) for item_id in ids] == samples


def test_stage5_sample_date_respects_leap_years_and_collision_nonce():
    from uuid import uuid5, NAMESPACE_URL

    review = str(uuid5(NAMESPACE_URL, "leap-year-sample"))
    anchor = datetime(2024, 2, 29, 15, 22, 46, tzinfo=timezone.utc)
    first = stage5_sample_review_date(review, anchor)
    second = stage5_sample_review_date(review, anchor, nonce=1)
    parsed = datetime.strptime(first, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    assert datetime(2022, 2, 28, 15, 22, 46, tzinfo=timezone.utc) <= parsed <= anchor
    assert first != second
    assert second == stage5_sample_review_date(review, anchor, nonce=1)
