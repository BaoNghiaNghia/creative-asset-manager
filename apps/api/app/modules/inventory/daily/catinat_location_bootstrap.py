"""Safe bootstrap of the three user-confirmed Catinat warehouse locations."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.inventory.persistence_model import InventoryLocationModel
from app.modules.inventory.repository import InventoryCatalogRepository

CONFIRMED_LOCATIONS = (
    ("CATINAT_KHO_PHA_CHE", "KHO PHA CHẾ"),
    ("CATINAT_KHO_PHONG_RANG", "KHO PHÒNG RANG"),
    ("CATINAT_PHONG_PHA_CHE", "PHÒNG PHA CHẾ"),
)


def bootstrap_confirmed_locations(session: Session, tenant_id: str, *, apply: bool = False) -> dict:
    existing = list(session.scalars(select(InventoryLocationModel).where(
        InventoryLocationModel.tenant_id == tenant_id,
    )))
    repository = InventoryCatalogRepository(session)
    result = []
    for code, name in CONFIRMED_LOCATIONS:
        by_name = [item for item in existing if item.name.strip().casefold() == name.casefold()]
        by_code = [item for item in existing if item.code == code]
        if len(by_name) > 1 or len(by_code) > 1 or (by_name and by_code and by_name[0].id != by_code[0].id):
            raise ValueError("ambiguous_confirmed_location")
        if by_code and by_code[0].name != name:
            raise ValueError("confirmed_location_code_conflict")
        if by_name and not by_name[0].active:
            raise ValueError("confirmed_location_inactive")
        if by_name:
            outcome = "existing"
        elif apply:
            repository.create_location(tenant_id, code=code, name=name)
            outcome = "created"
        else:
            outcome = "would_create"
        result.append({"name": name, "status": outcome})
    return {"applied": apply, "locations": result, "inventory_writes": 0, "sheet_writes": 0}
