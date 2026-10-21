from datetime import date
from types import SimpleNamespace

import pytest

from app.modules.inventory.daily_sheet.catinat_preview import (
    CatinatPreviewError, load_catinat_preview, serialize_preview, _metadata_size,
)

DAY = date(2026, 10, 19)


def _rows():
    result = [[] for _ in range(12)]
    result[2] = [None, "19/10/2026"]
    result[4] = [
        "STT", "Tên Nguyên Liệu / Vật Tư", "", "SL Đầu Ca / Nhận",
        "SL Sử Dụng Pha Chế", "", "Nhập Hàng", "SL Huỷ / Hư Hỏng", "", "",
        "Tồn Cuối Ca",
    ]
    result[10] = [3, "Tắc", None, "10", "8", "", "30", "0", "", "", ""]
    return result


def _metadata(row_count=120):
    return {"sheets": [{"properties": {
        "title": "Báo cáo sử dụng NVL trong ca",
        "gridProperties": {"rowCount": row_count},
    }}]}


def test_readonly_preview_is_unverified_never_an_autofill():
    result = serialize_preview(_rows(), DAY)
    assert result["mode"] == "read_only_unverified"
    assert result["auto_fill_allowed"] is False
    assert result["physical_count_confirmed"] is False
    assert result["suggestions"][0]["expected_stock"] == "32"
    assert result["suggestions"][0]["target"] == "K11"


def test_layout_date_mismatch_fails_closed():
    with pytest.raises(CatinatPreviewError, match="report_business_date_mismatch"):
        serialize_preview(_rows(), date(2026, 10, 20))


def test_report_must_exist_once_and_be_bounded():
    with pytest.raises(CatinatPreviewError, match="catinat_report_tab_missing"):
        _metadata_size({"sheets": []})
    with pytest.raises(CatinatPreviewError, match="catinat_report_size_unverified"):
        _metadata_size(_metadata(1000))


class FakeSession:
    def __init__(self, settings, source):
        self.settings, self.source, self.calls = settings, source, 0
    def __enter__(self):
        return self
    def __exit__(self, *_args):
        pass
    def scalar(self, _query):
        self.calls += 1
        return self.settings if self.calls == 1 else self.source


class FakeSheets:
    def __init__(self, token):
        assert token == "token"
        self.calls = []
    def __enter__(self):
        return self
    def __exit__(self, *_args):
        pass
    def spreadsheet_metadata(self, spreadsheet_id):
        self.calls.append(("metadata", spreadsheet_id))
        return _metadata()
    def batch_get_values(self, spreadsheet_id, ranges, *, value_render_option):
        self.calls.append(("read", spreadsheet_id, ranges, value_render_option))
        return [{"values": _rows()}]


def test_preview_reads_only_tenant_configured_workbook():
    settings = SimpleNamespace(daily_working_spreadsheet_file_id="authorized-workbook",
                               external_source_id="source-id")
    source = SimpleNamespace(source_metadata={"oauth_connection_id": "oauth-id"})
    sheets = []
    def factory(token):
        instance = FakeSheets(token)
        sheets.append(instance)
        return instance
    result = load_catinat_preview(
        "tenant-one", DAY,
        sessions=lambda: FakeSession(settings, source),
        token_resolver=lambda _: "token", client_factory=factory,
    )
    assert result["suggestions"][0]["expected_stock"] == "32"
    assert sheets[0].calls == [
        ("metadata", "authorized-workbook"),
        ("read", "authorized-workbook",
         ["'Báo cáo sử dụng NVL trong ca'!A1:K120"], "FORMATTED_VALUE"),
    ]


def test_preview_refuses_unconfigured_workbook():
    with pytest.raises(CatinatPreviewError, match="catinat_workbook_not_configured"):
        load_catinat_preview(
            "tenant", DAY,
            sessions=lambda: FakeSession(None, None),
            token_resolver=lambda _: "token",
        )


def test_preview_refuses_missing_connection():
    settings = SimpleNamespace(daily_working_spreadsheet_file_id="configured",
                               external_source_id="source-id")
    with pytest.raises(CatinatPreviewError, match="catinat_google_connection_missing"):
        load_catinat_preview(
            "tenant", DAY,
            sessions=lambda: FakeSession(settings, None),
            token_resolver=lambda _: "token",
        )
