"""Server-owned source roots for isolated Scout & UGC workspaces."""
from __future__ import annotations

from typing import Literal

ScoutWorkspace = Literal["gatorhats", "tiktokshop"]
RRUGC_SOURCE_ROOT_FOLDER_ID = "1kNBQU4O-i6cbDBnRrhPGNENHvieWYPfX"
TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID = "1LS8_v9HL-OmucwRFLcFwRU7iW5r3xJkh"
SOURCE_WORKSPACE_ROOTS: dict[str, str] = {
    "gatorhats": RRUGC_SOURCE_ROOT_FOLDER_ID,
    "tiktokshop": TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID,
}


def source_root_matches_workspace(root_folder_id: str | None, workspace: ScoutWorkspace) -> bool:
    """Legacy GatorHats routes also serve the Products/non-Reviews roots."""
    if workspace == "tiktokshop":
        return root_folder_id == TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID
    if workspace == "gatorhats":
        return bool(root_folder_id) and root_folder_id != TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID
    raise ValueError("Unknown Scout & UGC workspace")


def source_root_sql_clause(root_column, workspace: ScoutWorkspace):
    """SQL-side isolation: apply BEFORE count, limit, keyset and mutations."""
    if workspace == "tiktokshop":
        return root_column == TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID
    if workspace == "gatorhats":
        return root_column != TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID
    raise ValueError("Unknown Scout & UGC workspace")
