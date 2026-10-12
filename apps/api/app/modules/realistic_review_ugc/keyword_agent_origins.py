"""Tenant-scoped, idempotent source-agent evidence for Stage 0 keywords.

An Agent is distinct from the site it searched. One keyword can have several
agents and several source items; AEBrowse is only a metrics provider.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.model import RrugcKeywordAgentOriginModel


def record_keyword_agent(
    session: Session, *, tenant_id: str, keyword_normalized: str,
    agent_key: str, agent_name: str, source_platform: str,
    source_channel: str, source_url: str | None = None,
) -> bool:
    """Write a single evidence record; ignore exact retries without clobbering peers."""
    key = agent_key.strip()[:100]
    name = agent_name.strip()[:160]
    if not key or not name or not keyword_normalized:
        return False
    # Stable across repeats of an identical agent/source/keyword. If no URL,
    # one logical source is stored per keyword and agent.
    url = (source_url or "").strip()[:2048]
    fingerprint = sha256((source_platform + "|" + (url or keyword_normalized)).encode()).hexdigest()
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        insert = postgres_insert(RrugcKeywordAgentOriginModel)
    elif dialect == "sqlite":
        insert = sqlite_insert(RrugcKeywordAgentOriginModel)
    else:
        raise ValueError("Unsupported research database dialect")
    result = session.execute(insert.values(
        id=str(uuid4()), tenant_id=tenant_id,
        keyword_normalized=keyword_normalized, agent_key=key, agent_name=name,
        source_platform=source_platform[:32], source_channel=source_channel[:80],
        evidence_hash=fingerprint, source_url=url or None,
        first_seen_at=datetime.now(timezone.utc),
    ).on_conflict_do_nothing(
        index_elements=["tenant_id", "keyword_normalized", "agent_key", "evidence_hash"],
    ))
    return result.rowcount == 1


def origins_for_keywords(
    session: Session, *, tenant_id: str, keywords: list[str],
) -> dict[str, list[dict]]:
    """One bounded DB fetch per page, aggregated by authenticated agent identity."""
    keys = sorted(set(keywords))
    if not keys:
        return {}
    rows = session.scalars(select(RrugcKeywordAgentOriginModel).where(
        RrugcKeywordAgentOriginModel.tenant_id == tenant_id,
        RrugcKeywordAgentOriginModel.keyword_normalized.in_(keys),
    ).order_by(
        RrugcKeywordAgentOriginModel.first_seen_at.asc(),
        RrugcKeywordAgentOriginModel.id.asc(),
    )).all()
    grouped: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in rows:
        by_agent = grouped[row.keyword_normalized]
        item = by_agent.get(row.agent_key)
        if item is None:
            item = {
                "agent_key": row.agent_key, "agent_name": row.agent_name,
                "platforms": [], "evidence_count": 0,
            }
            by_agent[row.agent_key] = item
        item["evidence_count"] += 1
        if row.source_platform not in item["platforms"]:
            item["platforms"].append(row.source_platform)
    return {keyword: list(agents.values()) for keyword, agents in grouped.items()}
