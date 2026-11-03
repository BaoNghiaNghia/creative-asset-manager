"""Read-only Keyword Intelligence V2 preview for GatorHats Stage 0.

Three audited creativity skills inform the *questions*, not fabricated answers:
- Nurse: buyer / wearer / why buy
- Pet: emotional connection / compositional metaphor
- Embroidery Redesign: familiar typography DNA / one intentional twist / cap fit

Reuse an existing, current Creative Brief for AI hypotheses. Deterministic
preflight only when no brief exists. Never make automatic provider calls, queue
jobs, claim verified sales, or rewrite the original quote.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from app.modules.realistic_review_ugc.creative_brief_service import PROMPT_VERSION
from app.modules.realistic_review_ugc.keyword_volume import trademark_evidence_is_current
from app.modules.realistic_review_ugc.model import (
    RrugcKeywordCreativeBriefModel as CreativeBriefRow,
    RrugcKeywordVolumeModel as KeywordRow,
)

VERSION = "keyword-intelligence-v2-evidence-2"
WEIGHTS = {
    "buyer": 25,
    "creative": 25,
    "embroidery": 20,
    "market": 20,
    "originality": 10,
}
TITLES = {
    "buyer": "Buyer & Emotion",
    "creative": "Creative Potential",
    "embroidery": "Cap Embroidery Fit",
    "market": "Market Signals",
    "originality": "Visual Originality",
}
_WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)


def _bounded_score(value: int) -> int:
    return max(0, min(100, value))


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:1500] if value else None


def _current_brief(row: KeywordRow, brief: CreativeBriefRow | None) -> dict | None:
    if not brief or brief.status != "completed" or not isinstance(brief.brief_json, dict):
        return None
    if brief.keyword_sha256 != hashlib.sha256(row.keyword.encode("utf-8")).hexdigest():
        return None
    if brief.prompt_version != PROMPT_VERSION:
        return None
    if brief.brief_json.get("keyword_exact") != row.keyword:
        return None
    return brief.brief_json


def _market_score(row: KeywordRow) -> int | None:
    if row.provider != "aebrowse_google_ads":
        return None
    volume = max(0, int(row.search_volume or 0))
    score = 43 if volume == 0 else 54 if volume < 20 else 64 if volume < 100 else 76 if volume < 1000 else 83
    if (row.competition or "").casefold() == "high":
        score -= 7
    elif (row.competition or "").casefold() == "low":
        score += 3
    return _bounded_score(score)


def _fit_score(phrase: str) -> tuple[int, list[str]]:
    words = _WORD_RE.findall(phrase)
    count = len(phrase)
    score = 91 if count <= 22 else 81 if count <= 36 else 67 if count <= 50 else 48 if count <= 70 else 29
    warnings = []
    if len(words) >= 9:
        score -= 9
        warnings.append("Nhiều từ; cần phân cấp chữ rõ để đọc được ở kích thước mũ.")
    if count > 50:
        warnings.append("Câu dài; nguy cơ chữ nhỏ khi đặt trên crown cong.")
    if len(words) <= 2 and count >= 28:
        warnings.append("Từ dài; phải kiểm tra tỷ lệ chữ trên mặt mũ.")
    if not warnings:
        warnings.append("Độ dài tương đối thuận lợi; vẫn cần kiểm tra chữ và chiều rộng thêu thực tế.")
    return _bounded_score(score), warnings


def _creative_directions(brief: dict | None) -> tuple[list[dict], int]:
    if brief is None:
        return [], 0
    concepts = brief.get("concepts")
    if not isinstance(concepts, list):
        return [], 0
    directions = []
    names = {"safe": "Typography-first", "creative_twist": "Creative Twist", "explore": "Explore"}
    unique: set[str] = set()
    for concept in concepts:
        if not isinstance(concept, dict) or concept.get("mode") not in names:
            continue
        variants = [
            value.strip()[:450] for value in concept.get("design_variants", [])
            if isinstance(value, str) and value.strip()
        ] if isinstance(concept.get("design_variants"), list) else []
        unique.update(value.casefold() for value in variants)
        directions.append({
            "mode": concept["mode"],
            "label": names[concept["mode"]],
            "name": _text(concept.get("name")),
            "familiar_format": _text(concept.get("familiar_format")),
            "creative_twist": _text(concept.get("creative_twist")),
            "creative_hook": _text(concept.get("creative_hook")),
            "variants": variants,
        })
    return directions, len(unique)


def _component(key: str, value: int | None, source: str, note: str) -> dict:
    return {
        "key": key, "label": TITLES[key], "weight": WEIGHTS[key],
        "score": value, "source": source, "note": note,
    }


def build_keyword_intelligence(
    row: KeywordRow, brief_row: CreativeBriefRow | None,
    learning: dict | None = None,
    cross_learning: dict | None = None,
    visual_evidence: dict | None = None,
) -> dict:
    """Pure, bounded scoring; no market claims without real stored evidence."""
    brief = _current_brief(row, brief_row)
    directions, direction_count = _creative_directions(brief)
    fit_score, fit_warnings = _fit_score(row.keyword)
    buyer_ready = bool(brief and _text(brief.get("target_audience"))
                       and _text(brief.get("purchase_motivation")))
    buyer_score = 70 if buyer_ready else None
    creative_score = (
        86 if direction_count >= 9 else 74 if direction_count >= 6
        else 58 if direction_count >= 3 else 40 if direction_count else None
    )
    market_score = _market_score(row)
    dimensions = [
        _component("buyer", buyer_score, "ai_hypothesis" if buyer_ready else "missing",
                   "Giả thuyết người mua từ Creative Brief; chưa phải bằng chứng bán hàng."
                   if buyer_ready else "Chưa có Creative Brief đủ dữ liệu người mua."),
        _component("creative", creative_score, "creative_brief" if creative_score is not None else "missing",
                   f"Creative Brief có {direction_count} hướng mô tả khác nhau; chưa kiểm tra hình ảnh đầu ra."
                   if creative_score is not None else "Cần Creative Brief để đánh giá khả năng tạo 9 bố cục."),
        _component("embroidery", fit_score, "text_preflight",
                   "Ước lượng theo độ dài câu, không thay thế kiểm tra kích thước mũ và đường thêu."),
        _component("market", market_score, "google_ads" if market_score is not None else "missing",
                   "Chỉ dùng volume và mức cạnh tranh đã lưu; không suy ra doanh số Etsy."
                   if market_score is not None else "Chưa truy vấn dữ liệu volume từ nhà cung cấp."),
        _component("originality", None, "not_verified",
                   "Chưa chạy so sánh hình ảnh và Typography DNA. Không tự gán điểm Originality."),
    ]
    coverage = sum(item["weight"] for item in dimensions if item["score"] is not None)
    total_score = (
        round(sum(item["weight"] * item["score"] for item in dimensions
                  if item["score"] is not None) / coverage)
        if coverage >= 60 else None
    )
    trademark = row.trademark_status if trademark_evidence_is_current(row) else "unverified"
    if trademark == "danger":
        recommendation = "hold"
        explanation = "Nguồn screening gắn cờ xung đột trademark; cần kiểm tra thủ công trước khi dùng."
    elif total_score is None:
        recommendation = "needs_analysis"
        explanation = "Chưa đủ dữ liệu để chấm cơ hội; hãy phân tích Creative Brief và/hoặc lấy volume."
    elif total_score >= 80 and coverage >= 70 and market_score is not None and trademark in {"safe", "no_exact_match"}:
        recommendation = "prioritize"
        explanation = "Có tín hiệu đáng ưu tiên để thử thiết kế; điểm không phải dự báo doanh số."
    elif total_score < 53 and coverage >= 70:
        recommendation = "hold"
        explanation = "Cần xem lại độ dài hoặc chứng cứ nhu cầu trước khi chạy ảnh."
    else:
        recommendation = "explore"
        explanation = "Nên thử nghiệm có kiểm soát; chưa đủ bằng chứng để khẳng định tính thương mại."
    learn = learning if isinstance(learning, dict) else {
        "version": 1, "rejected": [], "picked": [],
        "general_rejections": [], "total_votes": 0, "rejected_hashes": [],
    }
    picked_votes = sum(int(item.get("count", 0)) for item in learn.get("picked", []))
    rejected_votes = sum(int(item.get("count", 0)) for item in learn.get("rejected", [])) + sum(
        int(item.get("count", 0)) for item in learn.get("general_rejections", [])
    )
    words = _WORD_RE.findall(row.keyword)
    market_raw = row.provider_raw_json if isinstance(row.provider_raw_json, dict) else {}
    visual_summary = dict(visual_evidence or {
        "status": "no_approved_g3_reference", "analyzed_count": 0,
        "items": [], "similar_pairs": [],
        "candidate_image_checked": False, "originality_verified": False,
    })
    visual_summary["rejected_stage1_fingerprint_count"] = len(learn.get("rejected_visual_hashes", []))
    return {
        "version": VERSION,
        "keyword_id": row.id,
        "keyword_exact": row.keyword,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "brief_status": (
            "ready" if brief is not None else
            "stale" if brief_row and brief_row.status == "completed" else
            brief_row.status if brief_row else "not_analyzed"
        ),
        "brief_updated_at": brief_row.updated_at.isoformat() if brief_row and brief_row.updated_at else None,
        "score": {
            "value": total_score, "coverage": coverage,
            "confidence": "medium" if brief is not None and market_score is not None else "low",
            "recommendation": recommendation, "explanation": explanation,
            "dimensions": dimensions,
            "is_preliminary": True,
        },
        "buyer": {
            "audience": _text(brief.get("target_audience")) if brief else None,
            "purchase_motivation": _text(brief.get("purchase_motivation")) if brief else None,
            "emotion": _text(brief.get("meaning_emotion")) if brief else None,
            "strategy": _text(brief.get("strategy")) if brief else None,
            "source": "ai_hypothesis" if brief else "not_analyzed",
        },
        "creative": {
            "hook": _text(brief.get("creative_hook")) if brief else None,
            "direction_count": direction_count,
            "directions": directions,
            "originality_checked": False,
        },
        "embroidery": {
            "word_count": len(words), "character_count": len(row.keyword),
            "preflight_score": fit_score, "warnings": fit_warnings,
            "physical_fit_verified": False,
        },
        "evidence": {
            "provider": row.provider, "volume": None if row.provider == "pending" else max(0, int(row.search_volume or 0)),
            "competition": row.competition,
            "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
            "three_month_change_pct": (
                float(market_raw["three_month_change_pct"])
                if row.provider == "aebrowse_google_ads"
                and type(market_raw.get("three_month_change_pct")) in (int, float) else None
            ),
            "yoy_change_pct": (
                float(market_raw["yoy_change_pct"])
                if row.provider == "aebrowse_google_ads"
                and type(market_raw.get("yoy_change_pct")) in (int, float) else None
            ),
            "source_pin_available": bool(row.source_pin_url),
            "source_image_available": bool(row.source_image_url),
            "trademark_status": trademark,
            "trademark_legal_clearance": False,
        },
        "visual": visual_summary,
        "learning": {
            "cross_keyword": cross_learning or {
                "version": 1, "cohort": None, "eligible": False,
                "distinct_keywords": 0, "vote_count": 0, "patterns": [],
            },
            "total_votes": learn.get("total_votes", 0),
            "picked_votes": picked_votes,
            "rejected_votes": rejected_votes,
            "rejected": learn.get("rejected", [])[:9],
            "general_rejections": learn.get("general_rejections", [])[:5],
            "scope": "tenant_and_exact_keyword",
        },
    }
