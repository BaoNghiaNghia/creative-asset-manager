"""Canonical source-plan grouping for Reviews Stage 3 and Stage 4.

Never join two distinct, currently analyzed embroidery identities merely
because a legacy Scout campaign was once shared. Legacy/unanalyzed rows
may join a campaign only when it identifies one unambiguous live design.

Grouping is read-only: no mutation of plans, campaigns, or job history.
"""
from __future__ import annotations

from collections import defaultdict

from app.modules.realistic_review_ugc.model import RrugcSourcePlanModel
from app.modules.realistic_review_ugc.source_plans import embroidery_signature


def group_source_plan_rows(
    rows: list[RrugcSourcePlanModel],
) -> list[list[RrugcSourcePlanModel]]:
    if not rows:
        return []

    parents = list(range(len(rows)))
    # A "strong" signature was recomputed from the analyzed image context;
    # persisted hashes without context are weaker legacy evidence.
    strong = [
        embroidery_signature(row.visual_context_json)
        if isinstance(row.visual_context_json, dict) else None
        for row in rows
    ]
    effective = [
        current or row.embroidery_signature for current, row in zip(strong, rows)
    ]
    signatures: list[set[str]] = [
        {value} if value else set() for value in strong
    ]

    def find(i: int) -> int:
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    def join(a: int, b: int) -> None:
        a, b = find(a), find(b)
        if a == b:
            return
        # Prevent transitive bridges through a missing/legacy-context plan.
        # Strong evidence from two different image analyses cannot merge.
        if signatures[a] and signatures[b] and signatures[a] != signatures[b]:
            return
        parents[b] = a
        signatures[a].update(signatures[b])

    # Never mix distinct tenants or source roots, even if a caller supplied
    # unfiltered rows or two tenants coincidentally share a signature.
    def scope(i: int) -> tuple[str, str]:
        return rows[i].tenant_id, rows[i].root_folder_id

    by_signature: dict[tuple[str, str, str], int] = {}
    for i, value in enumerate(effective):
        if not value:
            continue
        key = (*scope(i), value)
        if key in by_signature:
            join(i, by_signature[key])
        else:
            by_signature[key] = i

    campaigns: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    for i, row in enumerate(rows):
        if row.campaign_id:
            campaigns[(*scope(i), str(row.campaign_id))].append(i)

    for indexes in campaigns.values():
        # If the Scout campaign contains incompatible analyzed artwork,
        # it is stale/contaminated; it must not bridge the designs via any
        # weaker un-analyzed member.
        live_signatures = {strong[i] for i in indexes if strong[i]}
        if len(live_signatures) > 1:
            continue
        for i in indexes[1:]:
            join(indexes[0], i)

    groups: dict[int, list[RrugcSourcePlanModel]] = {}
    for i, row in enumerate(rows):
        groups.setdefault(find(i), []).append(row)
    return list(groups.values())
