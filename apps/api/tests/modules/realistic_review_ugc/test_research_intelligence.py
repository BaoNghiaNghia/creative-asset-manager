from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.realistic_review_ugc.model import RrugcResearchSignalModel
from app.modules.realistic_review_ugc.research_intelligence import (
    ResearchValidationError, import_signals, list_signals,
    normalize_keyword, review_signal, stage0_export, validate_url,
)


@pytest.fixture()
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[RrugcResearchSignalModel.__table__])
    with Session(engine) as db:
        yield db
    engine.dispose()


def _signal(url="https://news.google.com/rss/articles/example"):
    return {"source": "rss", "channel": "market", "title": "<b>CAPS</b> trending",
            "url": url, "excerpt": "Hats for the weekend"}


def test_research_signals_are_tenant_scoped_and_require_review(session):
    item = _signal()
    assert import_signals(session, tenant_id="tenant-a", user_id="user-a", signals=[item]) == {
        "added": 1, "duplicates": 0}
    assert import_signals(session, tenant_id="tenant-a", user_id="user-a", signals=[item]) == {
        "added": 0, "duplicates": 1}
    assert import_signals(session, tenant_id="tenant-b", user_id="user-b", signals=[item]) == {
        "added": 1, "duplicates": 0}
    a = list_signals(session, tenant_id="tenant-a")
    b = list_signals(session, tenant_id="tenant-b")
    assert len(a) == len(b) == 1
    assert a[0]["id"] != b[0]["id"]
    assert a[0]["title"] == "CAPS trending"
    assert stage0_export(session, tenant_id="tenant-a") == {"keywords": [], "force": False}
    with pytest.raises(LookupError):
        review_signal(session, tenant_id="tenant-b", signal_id=a[0]["id"],
                      user_id="user-b", decision="approved", keyword="MADE TO WORSHIP")
    assert review_signal(session, tenant_id="tenant-a", signal_id=a[0]["id"],
                         user_id="user-a", decision="approved",
                         keyword="MADE TO WORSHIP")["status"] == "approved"
    assert stage0_export(session, tenant_id="tenant-a") == {
        "keywords": ["MADE TO WORSHIP"], "force": False}
    assert stage0_export(session, tenant_id="tenant-b") == {"keywords": [], "force": False}
    with pytest.raises(ResearchValidationError):
        review_signal(session, tenant_id="tenant-a", signal_id=a[0]["id"],
                      user_id="user-a", decision="rejected", keyword=None)


def test_invalid_url_and_keyword_fail_closed(session):
    for url in ("http://news.google.com/a", "https://localhost/admin",
                "https://127.0.0.1/a", "https://foo.internal/a",
                "https://name:password@example.org/a"):
        with pytest.raises(ResearchValidationError):
            validate_url(url)
    for value in ("", "hello", "ONE TWO /;", "<script>"):
        with pytest.raises(ResearchValidationError):
            normalize_keyword(value)
    with pytest.raises(ResearchValidationError):
        import_signals(session,tenant_id="tenant-a",user_id="user-a",
                       signals=[_signal("http://localhost")])
    assert list_signals(session, tenant_id="tenant-a") == []


def test_export_has_no_auto_aebrowse_side_effect(session, monkeypatch):
    import httpx
    def denied(*_a, **_kw):
        raise AssertionError("Research import must not call an external provider")
    monkeypatch.setattr(httpx, "post", denied, raising=False)
    row = _signal()
    import_signals(session,tenant_id="test",user_id="test",signals=[row])
    selected = list_signals(session, tenant_id="test")[0]
    review_signal(session,tenant_id="test",user_id="operator",
                  signal_id=selected["id"],decision="approved",keyword="MORGAN WALLEN")
    assert stage0_export(session, tenant_id="test")["keywords"] == ["MORGAN WALLEN"]
