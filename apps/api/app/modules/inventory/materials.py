from __future__ import annotations
import re, unicodedata
from dataclasses import dataclass, asdict
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any, Callable, Mapping
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.modules.inventory.persistence_model import InventoryItemAliasModel, InventoryItemModel, InventoryMaterialCandidateModel, InventoryMaterialExternalIdentityModel, InventoryMaterialPackageConversionModel, inventory_utcnow

def normalize_material_text(value: object) -> str:
    text=unicodedata.normalize("NFKC",str(value or "")).strip().casefold()
    text="".join(c for c in unicodedata.normalize("NFD",text) if unicodedata.category(c)!="Mn")
    return re.sub(r"[^a-z0-9]+"," ",text).strip()

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
        row=self.session.scalar(select(InventoryMaterialCandidateModel).where(InventoryMaterialCandidateModel.tenant_id==tenant_id,InventoryMaterialCandidateModel.source_id==source_id,InventoryMaterialCandidateModel.external_key==external_key,InventoryMaterialCandidateModel.raw_name==raw_name))
        if row is None:
            row=InventoryMaterialCandidateModel(tenant_id=tenant_id,source_id=source_id,sheet=sheet,source_row=source_row,external_key=external_key,raw_name=raw_name,category=category,status=resolution.status,suggested_item_id=resolution.material_id,suggested_canonical_name=resolution.suggested_canonical_name,confidence=resolution.confidence,reasons_json=list(resolution.reasons),context_json=dict(context or {}));self.session.add(row)
        return row
    def approve(self,tenant_id:str,candidate_id:str,*,actor_id:str|None,item_id:str|None=None,canonical_name:str|None=None,preferred_unit:str|None=None,canonical_dimension:str|None=None):
        c=self.session.scalar(select(InventoryMaterialCandidateModel).where(InventoryMaterialCandidateModel.tenant_id==tenant_id,InventoryMaterialCandidateModel.id==candidate_id))
        if c is None:raise LookupError("material_candidate_not_found")
        item=self._item(tenant_id,item_id) if item_id else None
        if item_id and item is None:raise LookupError("material_not_found")
        if item is None:
            name=(canonical_name or c.suggested_canonical_name or c.raw_name).strip();unit=preferred_unit or "count"
            item=InventoryItemModel(tenant_id=tenant_id,sku=f"material-{c.id}",name=name,base_unit=unit,preferred_unit=unit,canonical_dimension=canonical_dimension or "count",category=c.category,first_seen_at=inventory_utcnow(),last_seen_at=inventory_utcnow(),metadata_json={"created_from_candidate":c.id});self.session.add(item);self.session.flush()
        normalized=normalize_material_text(c.raw_name)
        if not self._is_alias(tenant_id,item.id,normalized):self.session.add(InventoryItemAliasModel(tenant_id=tenant_id,item_id=item.id,alias=c.raw_name,normalized_alias=normalized))
        identity=self.session.scalar(select(InventoryMaterialExternalIdentityModel).where(InventoryMaterialExternalIdentityModel.tenant_id==tenant_id,InventoryMaterialExternalIdentityModel.source_type=="google_sheet",InventoryMaterialExternalIdentityModel.source_id==c.source_id,InventoryMaterialExternalIdentityModel.external_key==c.external_key))
        if identity is None:self.session.add(InventoryMaterialExternalIdentityModel(tenant_id=tenant_id,item_id=item.id,source_type="google_sheet",source_id=c.source_id,external_key=c.external_key,last_seen_name=c.raw_name))
        elif identity.item_id!=item.id:raise ValueError("external_identity_already_mapped")
        c.status="approved";c.suggested_item_id=item.id;c.context_json={**(c.context_json or {}),"approved_by":actor_id};item.last_seen_at=inventory_utcnow();self.session.flush();return item
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
