"""Keyword opportunity ranking uses SQL across all tenant records."""
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import and_, create_engine, select
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.creative_brief_service import PROMPT_VERSION
from app.modules.realistic_review_ugc.keyword_intelligence import build_keyword_intelligence
from app.modules.realistic_review_ugc.keyword_opportunity_ranking import opportunity_sql
from app.modules.realistic_review_ugc.model import (
    RrugcKeywordVolumeModel as K, RrugcKeywordCreativeBriefModel as B,
)


def _keyword(session, phrase, *, tenant="a", volume=0, provider="pending", competition=None, tm="unverified"):
    row = K(id=str(uuid4()), tenant_id=tenant, keyword=phrase,
            keyword_normalized=phrase.casefold(), provider=provider,
            search_volume=volume, competition=competition,
            trademark_status=tm)
    session.add(row)
    session.flush()
    return row


def _brief(session, row):
    brief = B(id=str(uuid4()), tenant_id=row.tenant_id, keyword_id=row.id,
              keyword_sha256=sha256(row.keyword.encode()).hexdigest(),
              prompt_version=PROMPT_VERSION, status="completed",
              brief_json={
                  "keyword_exact": row.keyword, "target_audience": "Example audience",
                  "purchase_motivation": "Gift", "meaning_emotion": "Warm",
                  "concepts": [{
                      "mode": mode,
                      "name": mode,
                      "design_variants": [f"{mode}-distinct-{i}" for i in range(count)],
                  } for mode, count in (("safe", 1), ("creative_twist", 4), ("explore", 4))],
              })
    session.add(brief)
    session.flush()
    return brief


def test_opportunity_sql_filters_and_sorts_before_limit_and_is_tenant_scoped():
    engine = create_engine("sqlite://")
    K.__table__.create(engine)
    B.__table__.create(engine)
    with Session(engine) as session:
        low = _keyword(session, "LONG PHRASE WITH MANY WORDS THAT WILL BE HARD TO EMBROIDER ON A SMALL CAP FRONT")
        high = _keyword(session, "MUSIC FAN", volume=300, provider="aebrowse_google_ads", tm="safe")
        middle = _keyword(session, "COWBOY", volume=5, provider="aebrowse_google_ads", tm="unverified")
        other = _keyword(session, "PRIVATE TENANT", tenant="b", volume=3000, provider="aebrowse_google_ads", tm="safe")
        for row in (low, high, middle, other):
            _brief(session, row)
        score, rec, coverage = opportunity_sql()
        rows = session.execute(
            select(K.id, score, rec, coverage)
            .outerjoin(B, and_(B.keyword_id == K.id, B.tenant_id == K.tenant_id))
            .where(K.tenant_id == "a")
            .order_by(score.desc().nulls_last(), K.id)
            .limit(1),
        ).all()
        assert len(rows) == 1
        assert rows[0][0] == high.id
        assert rows[0][2] in {"prioritize", "explore"}
        assert rows[0][3] == 90

        filtered = session.execute(
            select(K.id)
            .outerjoin(B, and_(B.keyword_id == K.id, B.tenant_id == K.tenant_id))
            .where(K.tenant_id == "a", rec == "needs_analysis")
        ).all()
        assert len(filtered) == 0  # all three had current Creative Brief

        for row in (high, middle, low):
            brief = session.scalar(select(B).where(B.keyword_id == row.id))
            preview = build_keyword_intelligence(row, brief)
            sql = session.execute(select(score, rec, coverage)
                .select_from(K)
                .outerjoin(B, and_(B.keyword_id == K.id, B.tenant_id == K.tenant_id))
                .where(K.id == row.id)).one()
            assert int(sql[0]) == preview["score"]["value"]
            assert sql[1] == preview["score"]["recommendation"]
            assert sql[2] == preview["score"]["coverage"]
    engine.dispose()


def test_opportunity_missing_data_remains_unranked_and_zero_volume_is_explorable():
    engine = create_engine("sqlite://")
    K.__table__.create(engine)
    B.__table__.create(engine)
    with Session(engine) as session:
        missing = _keyword(session, "PENDING")
        zero = _keyword(session, "ZERO VOLUME", provider="aebrowse_google_ads", volume=0)
        _brief(session, zero)
        score, rec, coverage = opportunity_sql()
        results = session.execute(select(K.id, score, rec, coverage)
            .outerjoin(B, and_(B.keyword_id == K.id, B.tenant_id == K.tenant_id))
            .where(K.tenant_id == "a")).all()
        by_id = {r[0]: r[1:] for r in results}
        assert by_id[missing.id][0] is None
        assert by_id[missing.id][1] == "needs_analysis"
        assert by_id[zero.id][0] is not None
        assert by_id[zero.id][1] != "hold"
    engine.dispose()
