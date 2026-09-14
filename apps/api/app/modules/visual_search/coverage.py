from __future__ import annotations
import asyncio
from collections import defaultdict
from dataclasses import dataclass
from app.modules.visual_search.coverage_repository import VisualCoverageResourceReader
from app.modules.visual_search.model_spec import VISUAL_SEARCH_BASELINE_DESCRIPTOR
class VisualCoverageUnavailable(RuntimeError): pass
KEYS=("discovered_images","imported_images","visual_eligible","visual_indexed_current","visual_index_missing","visual_index_stale","unsupported_images")
@dataclass(frozen=True)
class VisualCoverage: index_state:str; totals:dict[str,int|None]; sources:list[dict]; ratios:dict[str,float]
def _ok(doc,resource):
 d=VISUAL_SEARCH_BASELINE_DESCRIPTOR
 return doc.get("asset_id")==resource.asset_id and doc.get("content_sha256")==resource.content_hash and all(doc.get(k)==getattr(d,k) for k in ("embedding_schema_version","encoder_name","encoder_revision","preprocess_version","similarity")) and not doc.get("is_deleted") and not doc.get("is_hidden")
def _ratio(a,b): return a/b if b else 0.0
class VisualCoverageService:
 def __init__(self,session_factory,index): self.session_factory,self.index=session_factory,index
 def collect(self,tenant_id):
  with self.session_factory() as s: resources=VisualCoverageResourceReader(s).resources(tenant_id)
  buckets={}
  for r in resources: buckets.setdefault(r.external_source_id,{"source_id":r.external_source_id,"display_name":r.display_name,"source_type":r.source_type,**{k:0 for k in KEYS}})["discovered_images"]+=1
  for r in resources:
   b=buckets[r.external_source_id]
   if r.imported:b["imported_images"]+=1
   if r.unsupported:b["unsupported_images"]+=1
   if r.eligible:b["visual_eligible"]+=1
  totals={k:sum(b[k] for b in buckets.values()) for k in KEYS}
  try: docs=asyncio.run(self.index.scan_projection_metadata(tenant_id))
  except Exception:
   for k in ("visual_indexed_current","visual_index_missing","visual_index_stale"):totals[k]=None
   for b in buckets.values():
    for k in ("visual_indexed_current","visual_index_missing","visual_index_stale"): b[k]=None
    b["ratios"]={"import_coverage":_ratio(b["imported_images"],b["discovered_images"]),"eligible_visual_coverage":None,"whole_resource_searchable":None}
   return VisualCoverage("unavailable",totals,sorted(buckets.values(),key=lambda b:((b["display_name"] or ""),b["source_id"])),{"import_coverage":_ratio(totals["imported_images"],totals["discovered_images"]),"eligible_visual_coverage":0.0,"whole_resource_searchable":0.0})
  by=defaultdict(list)
  for d in docs:
   if d.get("tenant_id")==tenant_id:by[d.get("asset_id")].append(d)
  for r in resources:
   if not r.eligible:continue
   b=buckets[r.external_source_id]; ds=by[r.asset_id]
   b["visual_indexed_current" if any(_ok(d,r) for d in ds) else "visual_index_stale" if ds else "visual_index_missing"]+=1
  totals={k:sum(b[k] for b in buckets.values()) for k in KEYS}
  for b in buckets.values(): b["ratios"]={"import_coverage":_ratio(b["imported_images"],b["discovered_images"]),"eligible_visual_coverage":_ratio(b["visual_indexed_current"],b["visual_eligible"]),"whole_resource_searchable":_ratio(b["visual_indexed_current"],b["discovered_images"])}
  return VisualCoverage("available",totals,sorted(buckets.values(),key=lambda b:((b["display_name"] or ""),b["source_id"])),{"import_coverage":_ratio(totals["imported_images"],totals["discovered_images"]),"eligible_visual_coverage":_ratio(totals["visual_indexed_current"],totals["visual_eligible"]),"whole_resource_searchable":_ratio(totals["visual_indexed_current"],totals["discovered_images"])})
