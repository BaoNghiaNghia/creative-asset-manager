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
