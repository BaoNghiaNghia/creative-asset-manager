"""Server-side Stage 0 opportunity ranking over the ENTIRE tenant dataset.

Deterministic preflight, not a revenue forecast or empirically calibrated model: missing signals have null
coverage, and priority requires verified market evidence plus safe TM screen.
SQL sorting/filtering happens before LIMIT/OFFSET, never on one browser page.
"""
from __future__ import annotations

from sqlalchemy import case, func, literal, and_, or_

from app.modules.realistic_review_ugc.model import (
    RrugcKeywordVolumeModel as K,
    RrugcKeywordCreativeBriefModel as B,
)
from app.modules.realistic_review_ugc.creative_brief_service import PROMPT_VERSION


def opportunity_sql():
    phrase = func.trim(K.keyword)
    words = func.length(phrase) - func.length(func.replace(phrase, " ", "")) + 1
    chars = func.length(K.keyword)
    fit = case(
        (chars <= 22, 91), (chars <= 36, 81), (chars <= 50, 67),
        (chars <= 70, 48), else_=29,
    ) - case((words >= 9, 9), else_=0)
    creative_ready = and_(
        B.id.is_not(None),
        B.status == "completed",
        B.prompt_version == PROMPT_VERSION,
        B.brief_json["keyword_exact"].as_string() == K.keyword,
        # Current V3 validates the entire 1/4/4 creative structure before
        # setting completed; never read stale V1/V2 briefs here.
    )
    buyer_ready = and_(
        creative_ready,
        func.length(func.trim(func.coalesce(B.brief_json["target_audience"].as_string(), ""))) > 0,
        func.length(func.trim(func.coalesce(B.brief_json["purchase_motivation"].as_string(), ""))) > 0,
    )
    market_ready = K.provider == "aebrowse_google_ads"
    market = case(
        (K.search_volume <= 0, 43),
        (K.search_volume < 20, 54),
        (K.search_volume < 100, 64),
        (K.search_volume < 1000, 76),
        else_=83,
    ) + case(
        (func.lower(func.coalesce(K.competition, "")) == "high", -7),
        (func.lower(func.coalesce(K.competition, "")) == "low", 3),
        else_=0,
    )
    coverage = (
        literal(20)
        + case((buyer_ready, 25), else_=0)
        + case((creative_ready, 25), else_=0)
        + case((market_ready, 20), else_=0)
    )
    weighted = (
        20 * fit
        + case((buyer_ready, 25 * 70), else_=0)
        + case((creative_ready, 25 * 86), else_=0)
        + case((market_ready, 20 * market), else_=0)
    )
    score = case(
        (coverage >= 60, func.round(weighted * 1.0 / func.nullif(coverage, 0))),
        else_=None,
    )
    # The persisted status can outlive the original phrase. A screening is
    # current ONLY if the original raw keyword and status still match.
    tm_verified = and_(
        K.trademark_source == "aebrowse_google_ads",
        func.lower(func.trim(K.provider_raw_json["_trademark_screened_keyword"].as_string()))
            == func.lower(func.trim(K.keyword)),
        func.upper(K.provider_raw_json["trademark"]["status"].as_string())
            == func.upper(K.trademark_status),
    )
    recommendation = case(
        (and_(tm_verified, K.trademark_status == "danger"), "hold"),
        (score.is_(None), "needs_analysis"),
        (and_(score >= 80, coverage >= 70, market_ready, tm_verified,
              K.trademark_status.in_(("safe", "no_exact_match"))), "prioritize"),
        (and_(score < 53, coverage >= 70), "hold"),
        else_="explore",
    )
    return score, recommendation, coverage
