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
