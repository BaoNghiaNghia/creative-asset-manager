from __future__ import annotations
import argparse, json, math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
def _relevance(raw: Mapping[str, Any]) -> dict[str, int]:
    result = {}
    for asset_id, score in raw.items():
        if not isinstance(asset_id, str) or not asset_id or isinstance(score, bool) or not isinstance(score, int) or score < 0: raise ValueError("relevance must map non-empty asset IDs to non-negative integers")
        if score: result[asset_id] = score
    return result
def _results(raw: Any) -> list[str]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)): raise ValueError("each result set must be an array of asset IDs")
    seen=set(); output=[]
    for asset_id in raw:
        if not isinstance(asset_id, str) or not asset_id: raise ValueError("result asset IDs must be non-empty strings")
        if asset_id not in seen: seen.add(asset_id); output.append(asset_id)
    return output
def evaluate(qrels: Mapping[str, Mapping[str, Any]], results: Mapping[str, Any], *, k: int=10) -> dict[str, float|int]:
    if k <= 0 or not qrels: raise ValueError("positive k and labelled queries are required")
    if set(results)-set(qrels): raise ValueError("results contain unknown query IDs")
    precision=recall=ndcg=0.; empty=0
    for query_id, raw in qrels.items():
        rel=_relevance(raw)
        if not rel: raise ValueError(f"query {query_id!r} has no relevant assets")
        ranked=_results(results.get(query_id, []))[:k]
        empty += not ranked
        hits=sum(asset in rel for asset in ranked)
        precision += hits/k; recall += hits/len(rel)
        dcg=sum((2**rel[asset]-1)/math.log2(rank+2) for rank,asset in enumerate(ranked) if asset in rel)
        ideal=sorted(rel.values(), reverse=True)[:k]
        idcg=sum((2**score-1)/math.log2(rank+2) for rank,score in enumerate(ideal))
        ndcg += dcg/idcg
    count=len(qrels)
    return {"queries":count, f"precision_at_{k}":round(precision/count,6), f"recall_at_{k}":round(recall/count,6), f"ndcg_at_{k}":round(ndcg/count,6), "empty_result_rate":round(empty/count,6)}
def main():
    parser=argparse.ArgumentParser(description="Evaluate labelled Visual Search results offline.")
    parser.add_argument("--qrels", required=True, type=Path); parser.add_argument("--results", required=True, type=Path); parser.add_argument("--k", type=int, default=10)
    args=parser.parse_args()
    qrels=json.loads(args.qrels.read_text()); results=json.loads(args.results.read_text())
    if not isinstance(qrels, Mapping) or not isinstance(results, Mapping): raise ValueError("qrels and results must be JSON objects")
    print(json.dumps(evaluate(qrels, results, k=args.k), sort_keys=True))
if __name__ == "__main__": main()
