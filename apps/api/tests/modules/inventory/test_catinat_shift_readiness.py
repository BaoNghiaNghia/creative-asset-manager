from copy import deepcopy
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.authorization.principal import CurrentPrincipal
from app.modules.inventory.daily_sheet.catinat_preview import CatinatPreviewError
from app.modules.inventory.daily_sheet.catinat_shift_readiness import (
    REPORT_TAB, load_evening_readiness, serialize_evening_readiness,
)
from app.modules.inventory.daily_sheet.router import router

DAY = date(2026, 10, 20)


def matrix(width=15, n=8):
    return [[""] * width for _ in range(n)]


def sample():
    store = matrix()
    store[0][9] = "20/10/2026"
    store[2][11] = "Tồn cuối"
    store[4][0] = "SỮA ĐẶC"
    store[4][7] = "7"
    store[4][8] = "lon"
    store[4][10] = "gr"
    store[4][11] = ""  # Stock L5 is NOT verified and cannot default to zero.

    bar = matrix()
    bar[0][9] = "20/10/2026"
    bar[2][7] = "Tồn cuối"
    bar[4][0] = "SỮA ĐẶC*"
    bar[4][1] = "2"
    bar[4][5] = "6"
    bar[4][7] = "6"
    bar[4][8] = "lon"
    bar[4][10] = "gr"

    roast = matrix()
    roast[0][11] = "20/10/2026"
    roast[2][9] = "Tồn cuối"
    roast[5][0] = "PIZZA HẢI SẢN"
    roast[5][1] = "6"
    roast[5][9] = "6"
    roast[5][10] = "CÁI"
    roast[5][14] = "1( gãy)"  # Evidence of waste, NOT numeric loose stock.

    report = matrix(width=11)
    report[2][1] = "20/10/2026"
    report[4][0] = "STT"
    report[4][1] = "Tên Nguyên Liệu / Vật Tư"
    report[6][1] = "KEM CHEESE"
    report[6][3] = "20/10(200gr)"  # No hour; 96-hour deadline not proven.
    return {
        "KHO PHA CHẾ": store, "PHÒNG PHA CHẾ": bar,
        "KHO PHÒNG RANG": roast, REPORT_TAB: report,
    }


def preview(rows=None):
    return serialize_evening_readiness(
        spreadsheet_id="configured-sheet", business_date=DAY,
        ranges=rows if rows is not None else sample(),
    )


def test_real_warehouse_closing_column_positions_and_unverified_state():
    result = preview()
    assert result["shift"] == "evening"
    assert result["automatic_carry_forward_allowed"] is False
    assert result["staff_confirmation_available"] is False
    assert result["sheets_writes"] == 0
    cells = {(x["sheet"], x["cell"]): x for x in result["items"]}
    assert cells[("KHO PHA CHẾ", "L5")]["quantity"] is None
    assert cells[("KHO PHA CHẾ", "L5")]["status"] == "invalid_or_missing_physical"
    assert cells[("PHÒNG PHA CHẾ", "H5")]["quantity"] == "6"
    assert cells[("KHO PHÒNG RANG", "J6")]["quantity"] == "6"
    assert cells[("KHO PHÒNG RANG", "L6")]["quantity"] is None
    assert cells[("PHÒNG PHA CHẾ", "J5")]["quantity"] is None
    assert cells[("PHÒNG PHA CHẾ", "J5")]["status"] == "invalid_or_missing_physical"
    assert any(b["code"] == "physical_count_missing_or_ambiguous" for b in result["blockers"])
    assert any(b["code"] == "prepared_time_and_expiry_require_staff_verification"
               for b in result["blockers"])


def test_configured_units_trigger_count_even_if_no_stock_movements():
    data = sample()
    data["KHO PHA CHẾ"][6][0] = "NGUYÊN LIỆU CHƯA DÙNG"
    data["KHO PHA CHẾ"][6][8] = "gói"
    data["KHO PHA CHẾ"][6][10] = "g"
    result = preview(data)
    cells = {(x["sheet"], x["cell"]): x for x in result["items"]}
    assert cells[("KHO PHA CHẾ", "L7")]["quantity"] is None
    assert cells[("KHO PHA CHẾ", "M7")]["quantity"] is None


def test_verified_zero_numeric_and_slash_remain_unconfirmed():
    rows = sample()
    rows["KHO PHA CHẾ"][4][11] = "/"
    rows["PHÒNG PHA CHẾ"][4][7] = "0"
    result = preview(rows)
    looked = {(x["sheet"], x["cell"]): x for x in result["items"]}
    assert looked[("KHO PHA CHẾ", "L5")]["quantity"] == "0"
    assert looked[("PHÒNG PHA CHẾ", "H5")]["quantity"] == "0"
    assert looked[("KHO PHA CHẾ", "L5")]["blocker"] == "employee_physical_confirmation_required"


def test_mutating_any_input_invalidates_source_hash():
    a = sample()
    b = deepcopy(a)
    b["PHÒNG PHA CHẾ"][4][5] = "7"  # Staff updates receipt while review open.
    assert preview(a)["source_hash"] != preview(b)["source_hash"]


@pytest.mark.parametrize("which,index,position", [
    ("KHO PHA CHẾ", 0, 9),
    ("PHÒNG PHA CHẾ", 0, 9),
    ("KHO PHÒNG RANG", 0, 11),
    (REPORT_TAB, 2, 1),
])
def test_each_sheet_wrong_business_date_blocks(which,index,position):
    data = sample()
    data[which][index][position] = "19/10/2026"
    with pytest.raises(CatinatPreviewError, match="catinat_business_date_mismatch"):
        preview(data)


def test_missing_tab_or_changed_closing_heading_blocks():
    data = sample()
    del data["PHÒNG PHA CHẾ"]
    with pytest.raises(CatinatPreviewError, match="catinat_required_tabs_missing"):
        preview(data)
    data = sample()
    data["KHO PHA CHẾ"][2][11] = "Xuất kho"
    with pytest.raises(CatinatPreviewError, match="catinat_closing_column_layout_changed"):
        preview(data)


def test_unmapped_quantity_row_is_blocked():
    data = sample()
    data["KHO PHA CHẾ"][6][7] = "8"
    assert any(b["code"] == "unmapped_quantity_row" for b in preview(data)["blockers"])


class FakeSession:
    def __init__(self, settings, source):
        self.settings, self.source, self.calls = settings, source, 0
    def __enter__(self):
        return self
    def __exit__(self,*args):
        pass
    def scalar(self,*args):
        self.calls += 1
        return self.settings if self.calls == 1 else self.source


class FakeSheets:
    calls = []
    def __init__(self,token):
        assert token == "token"
    def __enter__(self):
        return self
    def __exit__(self,*args):
        pass
    def spreadsheet_metadata(self,spreadsheet_id):
        self.calls.append(("metadata",spreadsheet_id))
        return {"sheets":[{"properties":{"title":name,"gridProperties":{"rowCount":30}}}
                          for name in sample()]}
    def batch_get_values(self,spreadsheet_id,ranges,*,value_render_option):
        self.calls.append(("read",spreadsheet_id,tuple(ranges),value_render_option))
        rows = sample()
        return [{"values":rows[key]} for key in rows]


def test_live_read_is_tenant_bound_and_has_only_google_gets():
    FakeSheets.calls.clear()
    settings = SimpleNamespace(daily_working_spreadsheet_file_id="configured-sheet",
                               external_source_id="source-1")
    source = SimpleNamespace(source_metadata={"oauth_connection_id":"oauth-1"})
    result = load_evening_readiness(
        "tenant-a", DAY, sessions=lambda:FakeSession(settings,source),
        client_factory=FakeSheets,token_resolver=lambda oauth: "token",
    )
    assert result["items"]
    assert FakeSheets.calls[0] == ("metadata","configured-sheet")
    assert FakeSheets.calls[1][0:2] == ("read","configured-sheet")
    assert FakeSheets.calls[1][-1] == "FORMATTED_VALUE"
    assert len(FakeSheets.calls) == 2


def make_principal(perms):
    return CurrentPrincipal(
        "staff", "tenant-a", "membership", None, frozenset({"operator"}),
        frozenset(perms), False, "session", "test",
    )


def app_for(principal):
    app=FastAPI()
    app.include_router(router,prefix="/api/inventory")
    for route in app.routes:
        if getattr(route,"path","").startswith("/api/inventory"):
            for dep in route.dependant.dependencies:
                for sub in dep.dependencies:
                    app.dependency_overrides[sub.call] = lambda: principal
    return TestClient(app)


def test_endpoint_enforces_read_permission_and_no_post():
    path="/api/inventory/daily-sheet/catinat/shift/readiness?business_date=2026-10-20"
    assert app_for(make_principal(set())).get(path).status_code == 403
    client=app_for(make_principal({"inventory.read"}))
    with patch("app.modules.inventory.daily_sheet.router.load_evening_readiness",
               return_value=preview()) as loader:
        response=client.get(path)
    assert response.status_code == 200
    loader.assert_called_once()
    assert response.json()["automatic_carry_forward_allowed"] is False
    assert client.post(path).status_code == 405
