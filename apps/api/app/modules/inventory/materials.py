from __future__ import annotations
import hashlib
import json
import re, unicodedata
from dataclasses import dataclass, asdict
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any, Callable, Mapping
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from app.modules.inventory.persistence_model import InventoryItemAliasModel, InventoryItemModel, InventoryMaterialCandidateModel, InventoryMaterialExternalIdentityModel, InventoryMaterialPackageConversionModel, InventoryLocationModel, inventory_utcnow

def normalize_material_text(value: object) -> str:
    text=unicodedata.normalize("NFKC",str(value or "")).strip().casefold()
    text="".join(c for c in unicodedata.normalize("NFD",text) if unicodedata.category(c)!="Mn")
    return re.sub(r"[^a-z0-9]+"," ",text).strip()


def material_candidate_group_evidence_hash(
    rows: list[InventoryMaterialCandidateModel], item: InventoryItemModel,
) -> str:
    """Bind the exact reviewed source rows and catalog units, never stock approval."""
    payload = {
        "item": {
            "id": item.id, "name": item.name, "active": item.active,
            "base_unit": item.base_unit, "whole_unit": item.whole_unit,
            "fraction_unit": item.fraction_unit,
        },
        "rows": [{
            "id": row.id, "source_id": row.source_id, "sheet": row.sheet,
            "source_row": row.source_row, "external_key": row.external_key,
            "raw_name": row.raw_name, "category": row.category,
            "suggested_item_id": row.suggested_item_id,
            "review_evidence": (row.context_json or {}).get("review_evidence")
                if isinstance(row.context_json, dict) else None,
        } for row in sorted(rows, key=lambda value: value.id)],
    }
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()

@dataclass(frozen=True,slots=True)
class MaterialResolution:
    status:str; material_id:str|None; raw_name:str; suggested_canonical_name:str
    category:str|None; confidence:Decimal; reasons:tuple[str,...]; requires_review:bool
    interpretation_source:str="deterministic"
    def to_dict(self)->dict[str,Any]:
        value=asdict(self);value["confidence"]=float(self.confidence);value["reasons"]=list(self.reasons);return value

SemanticMatcher=Callable[[Mapping[str,Any]],Mapping[str,Any]|None]

class MaterialRegistry:
    def __init__(self,session:Session):self.session=session
    def list_materials(self,tenant_id:str):
        return list(self.session.scalars(select(InventoryItemModel).where(InventoryItemModel.tenant_id==tenant_id).order_by(InventoryItemModel.name,InventoryItemModel.id)))
    def list_candidates(self,tenant_id:str):
        return list(self.session.scalars(select(InventoryMaterialCandidateModel).where(InventoryMaterialCandidateModel.tenant_id==tenant_id,InventoryMaterialCandidateModel.status.in_(("new_material","possible_rename","ambiguous"))).order_by(InventoryMaterialCandidateModel.created_at,InventoryMaterialCandidateModel.id)))
    def readiness(self,tenant_id:str)->dict[str,Any]:
        """Read-only Inventory registry readiness; never infer a warehouse or approve materials."""
        materials=[item for item in self.list_materials(tenant_id) if item.active]
        candidates=self.list_candidates(tenant_id)
        locations=list(self.session.scalars(select(InventoryLocationModel).where(
            InventoryLocationModel.tenant_id==tenant_id,
        ).order_by(InventoryLocationModel.code)))
        active_locations=[item for item in locations if item.active]
        groups:dict[str,dict[str,Any]]={}
        for candidate in candidates:
            normalized=normalize_material_text(candidate.raw_name) or candidate.raw_name.strip().casefold()
            group=groups.setdefault(normalized,{
                "normalized_name":normalized,"sample_name":candidate.raw_name,
                "count":0,"statuses":set(),
            })
            group["count"]+=1
            group["statuses"].add(candidate.status)
        ranked=sorted(groups.values(),key=lambda row:(-row["count"],row["normalized_name"]))
        # Actionable groups are narrower than diagnostic name counts: never
        # batch-approve rows across workbook, category, sheet or tenant.
        exact_groups:dict[tuple[str,str,str],list[Any]]={}
        for candidate in candidates:
            group_key=(
                candidate.sheet,
                normalize_material_text(candidate.category),
                normalize_material_text(candidate.raw_name),
            )
            exact_groups.setdefault(group_key,[]).append(candidate)
        batch_groups=[
            {
                "candidate_id":rows[0].id,
                "candidate_ids":sorted(row.id for row in rows),
                "source_count":len({row.source_id for row in rows}),
                "sheet":sheet,"category":rows[0].category,
                "normalized_name":normalized,"sample_name":rows[0].raw_name,
                "count":len(rows),
            }
            for (sheet,category,normalized),rows in exact_groups.items()
            if len(rows)>1 and len(rows)<=100
            and len({row.suggested_item_id for row in rows if row.suggested_item_id})<=1
        ]
        batch_groups.sort(key=lambda row:(-row["count"],row["normalized_name"],row["sheet"]))
        blockers=[]
        if not materials:blockers.append("missing_material_registry")
        if not active_locations:blockers.append("missing_warehouse_registry")
        if candidates:blockers.append("unresolved_material_candidates")
        return {
            "active_materials":len(materials),
            "active_warehouses":len(active_locations),
            "pending_candidates":len(candidates),
            "distinct_candidate_names":len(groups),
            "duplicate_candidate_rows":len(candidates)-len(groups),
            "candidate_groups":[{
                "normalized_name":row["normalized_name"],"sample_name":row["sample_name"],
                "count":row["count"],"statuses":sorted(row["statuses"]),
            } for row in ranked[:100]],
            "batch_groups":batch_groups[:100],
            "warehouses":[{"id":row.id,"code":row.code,"name":row.name,"active":row.active}
                          for row in locations],
            "ready_for_carry_forward":not blockers,
            "blockers":blockers,
        }
    def create_warehouse(self,tenant_id:str,*,code:str,name:str)->InventoryLocationModel:
        """Explicit human-reviewed registry change, never inferred from AI."""
        code=str(code or "").strip().upper()
        name=str(name or "").strip()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{1,63}",code):
            raise ValueError("warehouse_code_invalid")
        if not name or len(name)>255:
            raise ValueError("warehouse_name_required")
        existing=self.session.scalar(select(InventoryLocationModel.id).where(
            InventoryLocationModel.tenant_id==tenant_id,InventoryLocationModel.code==code,
        ))
        if existing:raise ValueError("warehouse_code_already_exists")
        row=InventoryLocationModel(tenant_id=tenant_id,code=code,name=name,active=True)
        self.session.add(row)
        self.session.flush()
        return row

    def resolve(self,tenant_id:str,*,source_id:str,external_key:str,raw_name:str,category:str|None,source_row:int,sheet:str,semantic_matcher:SemanticMatcher|None=None,context:Mapping[str,Any]|None=None)->MaterialResolution:
        normalized=normalize_material_text(raw_name)
        identity=self.session.scalar(select(InventoryMaterialExternalIdentityModel).where(InventoryMaterialExternalIdentityModel.tenant_id==tenant_id,InventoryMaterialExternalIdentityModel.source_type=="google_sheet",InventoryMaterialExternalIdentityModel.source_id==source_id,InventoryMaterialExternalIdentityModel.external_key==external_key))
        if identity:
            item=self._item(tenant_id,identity.item_id)
            if item:
                renamed=normalize_material_text(item.name)!=normalized and not self._is_alias(tenant_id,item.id,normalized)
                return MaterialResolution("possible_rename" if renamed else "matched",item.id,raw_name,item.name,category,Decimal("1"),("approved_external_identity",)+(("name_changed",) if renamed else ()),renamed)
        matches={}
        for item in self.list_materials(tenant_id):
            if item.active and normalize_material_text(item.name)==normalized:matches[item.id]=item
        for alias in self.session.scalars(select(InventoryItemAliasModel).where(InventoryItemAliasModel.tenant_id==tenant_id,InventoryItemAliasModel.normalized_alias==normalized)):
            item=self._item(tenant_id,alias.item_id)
            if item:matches[item.id]=item
        if len(matches)==1:
            item=next(iter(matches.values()));return MaterialResolution("matched",item.id,raw_name,item.name,category,Decimal("1"),("approved_alias",),False)
        if len(matches)>1:return MaterialResolution("ambiguous",None,raw_name,raw_name,category,Decimal("1"),("multiple_approved_aliases",),True)
        scored=sorted(((SequenceMatcher(None,normalized,normalize_material_text(i.name)).ratio(),i) for i in self.list_materials(tenant_id) if i.active),key=lambda x:(-x[0],x[1].id))
        if scored and scored[0][0]>=.88 and (len(scored)==1 or scored[0][0]-scored[1][0]>=.08):
            score,item=scored[0];return MaterialResolution("possible_rename",item.id,raw_name,item.name,category,Decimal(str(round(score,6))),("deterministic_name_similarity",),True)
        if semantic_matcher:
            proposal=semantic_matcher({"tenant_id":tenant_id,"raw_row":{"item_key":external_key,"name":raw_name,"category":category,"row":source_row,"sheet":sheet},"source_context":dict(context or {}),"candidates":[{
                "material_id": i.id,
                "canonical_name": i.name,
                "category": i.category,
                "canonical_dimension": i.canonical_dimension,
                "preferred_unit": i.preferred_unit or i.base_unit,
                "aliases": self._aliases(tenant_id, i.id),
                "approved_package_conversions": {
                    package: {"canonical_value": str(value), "canonical_unit": unit}
                    for package, (value, unit) in self.approved_package_conversions(tenant_id, i.id).items()
                },
            } for _, i in scored[:8]]})
            if proposal:
                mid=str(proposal.get("material_id") or "") or None
                return MaterialResolution("possible_rename" if mid else "new_material",mid,raw_name,str(proposal.get("suggested_canonical_name") or raw_name),category,Decimal(str(proposal.get("confidence") or 0)),tuple(proposal.get("reasons") or ("gemini_semantic_candidate",)),True,"gemini")
        return MaterialResolution("new_material",None,raw_name,raw_name,category,Decimal("0"),("no_approved_match",),True)
    def queue_candidate(self,tenant_id:str,*,source_id:str,external_key:str,raw_name:str,category:str|None,source_row:int,sheet:str,resolution:MaterialResolution,context:Mapping[str,Any]|None=None):
        row=self.session.scalar(select(InventoryMaterialCandidateModel).where(
            InventoryMaterialCandidateModel.tenant_id==tenant_id,
            InventoryMaterialCandidateModel.source_id==source_id,
            InventoryMaterialCandidateModel.external_key==external_key,
            InventoryMaterialCandidateModel.raw_name==raw_name,
        ).with_for_update().execution_options(populate_existing=True))
        if row is None:
            row=InventoryMaterialCandidateModel(tenant_id=tenant_id,source_id=source_id,sheet=sheet,source_row=source_row,external_key=external_key,raw_name=raw_name,category=category,status=resolution.status,suggested_item_id=resolution.material_id,suggested_canonical_name=resolution.suggested_canonical_name,confidence=resolution.confidence,reasons_json=list(resolution.reasons),context_json=dict(context or {}));self.session.add(row)
        elif context and row.status in {"new_material","possible_rename","ambiguous"}:
            row.context_json={**dict(row.context_json or {}),**dict(context)}
        return row
    def approve_equivalent(
        self, tenant_id: str, *, candidate_id: str, item_id: str,
        actor_id: str | None, expected_count: int,
        expected_candidate_ids: list[str],
    ) -> dict[str, Any]:
        """Confirm the exact reviewed IDs across source snapshots with the same sheet/name/category.

        The count plus the complete ID set prevents approving unseen rows.
        Callers must explicitly select the destination material and commit.
        """
        seed = self.session.scalar(select(InventoryMaterialCandidateModel).where(
            InventoryMaterialCandidateModel.tenant_id == tenant_id,
            InventoryMaterialCandidateModel.id == candidate_id,
        ))
        if seed is None:
            raise LookupError("material_candidate_not_pending")
        seed_sheet = seed.sheet
        # Share the batch route's candidate -> catalog -> registry lock order.
        # Locking an arbitrary seed first can deadlock an overlapping selection.
        candidates = list(self.session.scalars(select(InventoryMaterialCandidateModel).where(
            InventoryMaterialCandidateModel.tenant_id == tenant_id,
            or_(InventoryMaterialCandidateModel.sheet == seed_sheet,
                InventoryMaterialCandidateModel.id == candidate_id),
        ).order_by(InventoryMaterialCandidateModel.id).with_for_update()
          .execution_options(populate_existing=True)))
        seed = next((row for row in candidates if row.id == candidate_id), None)
        if seed is None or seed.status not in {"new_material", "possible_rename", "ambiguous"}:
            raise LookupError("material_candidate_not_pending")
        if seed.sheet != seed_sheet:
            raise ValueError("candidate_group_changed")
        group = [
            c for c in candidates
            if c.status in {"new_material", "possible_rename", "ambiguous"}
            and c.sheet == seed_sheet
            and normalize_material_text(c.raw_name) == normalize_material_text(seed.raw_name)
            and normalize_material_text(c.category) == normalize_material_text(seed.category)
        ]
        if (not 1 <= expected_count <= 100 or len(group) != expected_count
            or sorted(c.id for c in group) != sorted(expected_candidate_ids)):
            raise ValueError("candidate_group_changed")
        material = self.session.scalar(select(InventoryItemModel).where(
            InventoryItemModel.tenant_id == tenant_id,
            InventoryItemModel.id == item_id,
            InventoryItemModel.active.is_(True),
        ).with_for_update().execution_options(populate_existing=True))
        if material is None:
            raise LookupError("material_not_found")
        if any(c.suggested_item_id and c.suggested_item_id != item_id for c in group):
            raise ValueError("candidate_conflicting_suggestion")
        for candidate in group:
            self.approve(tenant_id, candidate.id, actor_id=actor_id, item_id=item_id)
        return {"item_id": material.id, "approved": len(group)}


    def approve_equivalent_batch(
        self, tenant_id: str, *, groups: list[dict[str, Any]], actor_id: str,
    ) -> dict[str, Any]:
        """Approve one explicitly reviewed batch atomically and safely on replay.

        Source evidence and catalog units must still match the manager's preview.
        Ordered row locks precede all checks and registry writes. The caller owns
        the database transaction; no stock, conversion or Sheets write is made.
        """
        if not actor_id:
            raise ValueError("candidate_approval_actor_required")
        if not isinstance(groups, list) or not 1 <= len(groups) <= 50:
            raise ValueError("candidate_batch_size_invalid")
        hash_pattern = re.compile(r"^[0-9a-f]{64}$")
        seen_ids: set[str] = set()
        specs: list[dict[str, Any]] = []
        for spec in groups:
            if not isinstance(spec, dict):
                raise ValueError("candidate_batch_payload_invalid")
            candidate_id = spec.get("candidate_id")
            item_id = spec.get("item_id")
            expected_ids = spec.get("expected_candidate_ids")
            expected_count = spec.get("expected_count")
            evidence_hash = spec.get("expected_evidence_hash")
            if (
                not isinstance(candidate_id, str) or not candidate_id
                or not isinstance(item_id, str) or not item_id
                or not isinstance(expected_ids, list) or not 1 <= len(expected_ids) <= 100
                or type(expected_count) is not int or expected_count != len(expected_ids)
                or any(not isinstance(value, str) or not value for value in expected_ids)
                or len(set(expected_ids)) != len(expected_ids) or candidate_id not in expected_ids
                or not isinstance(evidence_hash, str) or not hash_pattern.fullmatch(evidence_hash)
            ):
                raise ValueError("candidate_batch_payload_invalid")
            if seen_ids.intersection(expected_ids):
                raise ValueError("candidate_batch_duplicate_selection")
            seen_ids.update(expected_ids)
            specs.append({
                "candidate_id": candidate_id, "item_id": item_id,
                "expected_count": expected_count, "expected_candidate_ids": sorted(expected_ids),
                "expected_evidence_hash": evidence_hash,
            })
        specs.sort(key=lambda spec: spec["candidate_id"])
        batch_hash = hashlib.sha256(json.dumps(
            {"tenant_id": tenant_id, "actor_id": actor_id, "groups": specs},
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        selected = list(self.session.scalars(select(InventoryMaterialCandidateModel).where(
            InventoryMaterialCandidateModel.tenant_id == tenant_id,
            InventoryMaterialCandidateModel.id.in_(seen_ids),
        )))
        if len(selected) != len(seen_ids):
            raise LookupError("material_candidate_not_found")
        sheets = {row.sheet for row in selected}
        # Lock every potentially matching row in one stable order. Locking each
        # group's seed first can deadlock two overlapping manager selections.
        records = list(self.session.scalars(select(InventoryMaterialCandidateModel).where(
            InventoryMaterialCandidateModel.tenant_id == tenant_id,
            or_(InventoryMaterialCandidateModel.sheet.in_(sheets),
                InventoryMaterialCandidateModel.id.in_(seen_ids)),
        ).order_by(InventoryMaterialCandidateModel.id).with_for_update()
          .execution_options(populate_existing=True)))
        by_id = {row.id: row for row in records}
        if any(row_id not in by_id for row_id in seen_ids):
            raise LookupError("material_candidate_not_found")
        if any(by_id[row_id].sheet not in sheets for row_id in seen_ids):
            raise ValueError("candidate_group_changed")
        selected = [by_id[row_id] for row_id in seen_ids]
        results = [{
            "item_id": spec["item_id"], "approved": spec["expected_count"],
        } for spec in specs]

        def result(replay: bool) -> dict[str, Any]:
            return {
                "status": "approved", "approved_groups": len(specs),
                "approved_candidates": len(seen_ids), "results": results,
                "idempotent_replay": replay,
                "inventory_balance_writes": 0, "google_sheet_writes": 0,
            }

        if any(row.status == "approved" for row in selected):
            for spec in specs:
                for row_id in spec["expected_candidate_ids"]:
                    row = by_id[row_id]
                    context = row.context_json if isinstance(row.context_json, dict) else {}
                    saved = context.get("bulk_approval")
                    if (
                        row.status != "approved" or row.suggested_item_id != spec["item_id"]
                        or context.get("approved_by") != actor_id
                        or not isinstance(saved, dict) or saved.get("batch_hash") != batch_hash
                    ):
                        raise ValueError("candidate_batch_idempotency_conflict")
            return result(True)

        catalog = list(self.session.scalars(select(InventoryItemModel).where(
            InventoryItemModel.tenant_id == tenant_id,
            InventoryItemModel.active.is_(True),
        ).order_by(InventoryItemModel.id).with_for_update()
          .execution_options(populate_existing=True)))

        def strict_text(value: object) -> str:
            text = unicodedata.normalize("NFC", str(value or "")).strip().casefold()
            return re.sub(r"\s+", " ", text)

        prepared: list[tuple[dict[str, Any], list[InventoryMaterialCandidateModel]]] = []
        for spec in specs:
            seed = by_id[spec["candidate_id"]]
            if seed.status not in {"new_material", "possible_rename", "ambiguous"}:
                raise LookupError("material_candidate_not_pending")
            actual = [
                row for row in records
                if row.status in {"new_material", "possible_rename", "ambiguous"}
                and row.sheet == seed.sheet
                and normalize_material_text(row.raw_name) == normalize_material_text(seed.raw_name)
                and normalize_material_text(row.category) == normalize_material_text(seed.category)
            ]
            if sorted(row.id for row in actual) != spec["expected_candidate_ids"]:
                raise ValueError("candidate_group_changed")
            matches = [
                item for item in catalog
                if normalize_material_text(item.name) == normalize_material_text(seed.raw_name)
            ]
            if len(matches) != 1:
                raise ValueError("candidate_catalog_match_ambiguous")
            item = matches[0]
            if item.id != spec["item_id"]:
                raise ValueError("candidate_catalog_match_changed")
            if material_candidate_group_evidence_hash(actual, item) != spec["expected_evidence_hash"]:
                raise ValueError("candidate_group_evidence_changed")
            allowed_units = {
                strict_text(value) for value in (item.base_unit, item.whole_unit, item.fraction_unit)
                if strict_text(value)
            }
            for row in actual:
                if strict_text(row.raw_name) != strict_text(item.name):
                    raise ValueError("candidate_name_not_exact")
                if row.suggested_item_id and row.suggested_item_id != item.id:
                    raise ValueError("candidate_conflicting_suggestion")
                context = row.context_json if isinstance(row.context_json, dict) else {}
                review = context.get("review_evidence")
                if not isinstance(review, dict) or review.get("status") != "explicit_unit_found":
                    raise ValueError("candidate_unit_evidence_missing")
                evidence = review.get("unit_evidence")
                if not isinstance(evidence, list) or not evidence:
                    raise ValueError("candidate_unit_evidence_missing")
                for unit in evidence:
                    if (
                        not isinstance(unit, dict) or not strict_text(unit.get("value"))
                        or not strict_text(unit.get("value_cell"))
                        or not strict_text(unit.get("header_cell"))
                        or not isinstance(unit.get("evidence_hash"), str)
                        or not hash_pattern.fullmatch(unit["evidence_hash"])
                    ):
                        raise ValueError("candidate_unit_evidence_invalid")
                    if strict_text(unit["value"]) not in allowed_units:
                        raise ValueError("candidate_unit_not_certified")
            prepared.append((spec, actual))

        for spec, rows in prepared:
            for row in rows:
                self.approve(tenant_id, row.id, actor_id=actor_id, item_id=spec["item_id"])
                row.context_json = {
                    **dict(row.context_json or {}),
                    "bulk_approval": {
                        "batch_hash": batch_hash,
                        "group_evidence_hash": spec["expected_evidence_hash"],
                    },
                }
        self.session.flush()
        return result(False)

    def approve(
        self,
        tenant_id: str,
        candidate_id: str,
        *,
        actor_id: str | None,
        item_id: str | None = None,
        canonical_name: str | None = None,
        preferred_unit: str | None = None,
        canonical_dimension: str | None = None,
    ):
        # Every approval path acquires candidate -> catalog locks before an
        # alias/identity flush, and rechecks state after a competing transaction.
        c = self.session.scalar(
            select(InventoryMaterialCandidateModel).where(
                InventoryMaterialCandidateModel.tenant_id == tenant_id,
                InventoryMaterialCandidateModel.id == candidate_id,
            ).with_for_update().execution_options(populate_existing=True)
        )
        if c is None:
            raise LookupError("material_candidate_not_found")
        if c.status not in {"new_material", "possible_rename", "ambiguous"}:
            raise ValueError("material_candidate_not_pending")
        item = self.session.scalar(select(InventoryItemModel).where(
            InventoryItemModel.tenant_id == tenant_id,
            InventoryItemModel.id == item_id,
            InventoryItemModel.active.is_(True),
        ).with_for_update().execution_options(populate_existing=True)) if item_id else None
        if item_id and item is None:
            raise LookupError("material_not_found")
        if item is None:
            name = (canonical_name or c.suggested_canonical_name or c.raw_name).strip()
            unit = str(preferred_unit or "").strip()
            dimension = str(canonical_dimension or "").strip()
            if not unit or not dimension:
                raise ValueError("material_unit_and_dimension_required")
            item = InventoryItemModel(
                tenant_id=tenant_id,
                sku=f"material-{c.id}",
                name=name,
                base_unit=unit,
                preferred_unit=unit,
                canonical_dimension=dimension,
                category=c.category,
                first_seen_at=inventory_utcnow(),
                last_seen_at=inventory_utcnow(),
                metadata_json={"created_from_candidate": c.id},
            )
            self.session.add(item)
            self.session.flush()
        normalized = normalize_material_text(c.raw_name)
        if not self._is_alias(tenant_id, item.id, normalized):
            self.session.add(
                InventoryItemAliasModel(
                    tenant_id=tenant_id,
                    item_id=item.id,
                    alias=c.raw_name,
                    normalized_alias=normalized,
                )
            )
        identity = self.session.scalar(
            select(InventoryMaterialExternalIdentityModel).where(
                InventoryMaterialExternalIdentityModel.tenant_id == tenant_id,
                InventoryMaterialExternalIdentityModel.source_type == "google_sheet",
                InventoryMaterialExternalIdentityModel.source_id == c.source_id,
                InventoryMaterialExternalIdentityModel.external_key == c.external_key,
            )
        )
        if identity is None:
            self.session.add(
                InventoryMaterialExternalIdentityModel(
                    tenant_id=tenant_id,
                    item_id=item.id,
                    source_type="google_sheet",
                    source_id=c.source_id,
                    external_key=c.external_key,
                    last_seen_name=c.raw_name,
                )
            )
        elif identity.item_id != item.id:
            raise ValueError("external_identity_already_mapped")
        c.status = "approved"
        c.suggested_item_id = item.id
        c.context_json = {**(c.context_json or {}), "approved_by": actor_id}
        item.last_seen_at = inventory_utcnow()
        self.session.flush()
        return item
    def package_conversion(self,tenant_id:str,item_id:str,package_name:str):
        return self.session.scalar(select(InventoryMaterialPackageConversionModel).where(InventoryMaterialPackageConversionModel.tenant_id==tenant_id,InventoryMaterialPackageConversionModel.item_id==item_id,InventoryMaterialPackageConversionModel.normalized_package==normalize_material_text(package_name)))
    def _item(self,tenant_id,item_id):
        if not item_id:return None
        return self.session.scalar(select(InventoryItemModel).where(InventoryItemModel.tenant_id==tenant_id,InventoryItemModel.id==item_id,InventoryItemModel.active.is_(True)))
    def _aliases(self,tenant_id,item_id):return list(self.session.scalars(select(InventoryItemAliasModel.alias).where(InventoryItemAliasModel.tenant_id==tenant_id,InventoryItemAliasModel.item_id==item_id)))
    def _is_alias(self,tenant_id,item_id,normalized):return self.session.scalar(select(InventoryItemAliasModel.id).where(InventoryItemAliasModel.tenant_id==tenant_id,InventoryItemAliasModel.item_id==item_id,InventoryItemAliasModel.normalized_alias==normalized)) is not None

    def approved_package_conversions(self, tenant_id: str, item_id: str) -> dict[str, tuple[Decimal, str]]:
        rows = self.session.scalars(select(InventoryMaterialPackageConversionModel).where(
            InventoryMaterialPackageConversionModel.tenant_id == tenant_id,
            InventoryMaterialPackageConversionModel.item_id == item_id,
        ))
        return {row.normalized_package: (row.canonical_value, row.canonical_unit) for row in rows}

    def observe_match(self, tenant_id: str, *, source_id: str, external_key: str, raw_name: str, material_id: str) -> None:
        item = self._item(tenant_id, material_id)
        if item is None:
            raise LookupError("material_not_found")
        identity = self.session.scalar(select(InventoryMaterialExternalIdentityModel).where(
            InventoryMaterialExternalIdentityModel.tenant_id == tenant_id,
            InventoryMaterialExternalIdentityModel.source_type == "google_sheet",
            InventoryMaterialExternalIdentityModel.source_id == source_id,
            InventoryMaterialExternalIdentityModel.external_key == external_key,
        ))
        if identity is None:
            self.session.add(InventoryMaterialExternalIdentityModel(
                tenant_id=tenant_id, item_id=item.id, source_type="google_sheet",
                source_id=source_id, external_key=external_key, last_seen_name=raw_name,
            ))
        elif identity.item_id != item.id:
            raise ValueError("external_identity_already_mapped")
        else:
            identity.last_seen_name = raw_name
        item.last_seen_at = inventory_utcnow()

    def describe_materials(self, tenant_id: str) -> list[dict[str, Any]]:
        identities = list(self.session.scalars(select(InventoryMaterialExternalIdentityModel).where(InventoryMaterialExternalIdentityModel.tenant_id == tenant_id)))
        aliases = list(self.session.scalars(select(InventoryItemAliasModel).where(InventoryItemAliasModel.tenant_id == tenant_id)))
        conversions = list(self.session.scalars(select(InventoryMaterialPackageConversionModel).where(InventoryMaterialPackageConversionModel.tenant_id == tenant_id)))
        return [{
            "material_id": item.id, "canonical_name": item.name, "category": item.category,
            "canonical_dimension": item.canonical_dimension, "preferred_unit": item.preferred_unit or item.base_unit,
            "active": item.active, "first_seen_at": item.first_seen_at, "last_seen_at": item.last_seen_at,
            "metadata": dict(item.metadata_json or {}),
            "sheet_keys": [row.external_key for row in identities if row.item_id == item.id],
            "aliases": [row.alias for row in aliases if row.item_id == item.id],
            "package_conversions": [{"package_name": row.package_name, "canonical_value": str(row.canonical_value), "canonical_unit": row.canonical_unit} for row in conversions if row.item_id == item.id],
        } for item in self.list_materials(tenant_id)]
